"""下载完成检测与入库整理（媒体库 L2 的调度入口）。

定时任务：把 grabbed/downloaded 且带 infohash 的工单按种子分组，逐个查询
下载器进度——

  grabbed ──下载器确认完成──→ downloaded ──整理器硬链+落账──→ imported

失败语义：
- 种子在所有可用下载器中都查不到（被手动删除）→ 工单退回 wanted 短冷却
  后重新找资源，并记中文活动说明；
- 整理失败（无库/跨盘/路径不可达）→ 记 IMPORT_FAILED 活动，指数退避重试，
  文件滞留下载区绝不误删。
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_api.services.library_import import (
    ImportOutcome,
    LibraryImportError,
    import_completed_torrent,
)
from movieclaw_api.services.subscription import recompute_subscription_status
from movieclaw_db.engine import get_database
from movieclaw_db.models import (
    ActivityType,
    MediaItem,
    Subscription,
    SubscriptionActivity,
    WantedItem,
    WantedStatus,
    utcnow,
)
from movieclaw_db.models.downloader_client import DownloaderClient
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.models.site_credential import ConfigStatus
from movieclaw_db.repositories import SubscriptionRepository
from movieclaw_db.repositories.downloader_repo import DownloaderRepository
from movieclaw_downloader import DownloaderConfig, TorrentStatus, create_downloader
from movieclaw_scheduler.registry import register_task

logger = logging.getLogger("movieclaw_api.download_progress")

# 轮询节奏：下载耗时以分钟/小时计，60 秒足够灵敏且对下载器无压力
PROGRESS_TICK_SECONDS = 60

# 种子被手动删除后工单退回 wanted 的冷却（给用户留出"删错了重新添加"的窗口）
_MISSING_RETRY_MINUTES = 30

# 整理失败的指数退避：5 分钟起，翻倍至 2 小时封顶。key=info_hash，
# 进程内存即可（重启后立即重试一次是无害且合理的）
_import_backoff: dict[str, tuple[float, float]] = {}
_BACKOFF_INITIAL = 300.0
_BACKOFF_MAX = 7200.0

_tick_lock = asyncio.Lock()


@register_task(
    "check_download_progress",
    title="下载完成检测与入库",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=PROGRESS_TICK_SECONDS,
    description=(
        "轮询下载器中订阅投递的种子：下载完成的推进工单状态，并触发整理器"
        "把文件硬链进媒体库（规范命名 + 介质探测 + 台账落账）。"
    ),
)
async def check_download_progress() -> None:
    async with _tick_lock:
        db = get_database()
        async with db.session() as session:
            groups = await _pipeline_groups(session)
            if not groups:
                return
            downloaders = await _usable_downloaders(session)
        if not downloaders:
            logger.warning("有 %d 个种子等待完成检测，但没有可用的下载器", len(groups))
            return
        for subscription_id, info_hash in groups:
            try:
                await _process_group(subscription_id, info_hash, downloaders)
            except Exception:  # noqa: BLE001 -- 单组失败不拖垮整轮
                logger.exception("种子 %s（订阅 #%s）的完成检测失败", info_hash, subscription_id)


async def _pipeline_groups(
    session: AsyncSession,
) -> dict[tuple[int, str], list[WantedItem]]:
    """管线中的工单，按（订阅, 种子）分组。"""
    result = await session.execute(
        select(WantedItem).where(
            WantedItem.status.in_([WantedStatus.GRABBED, WantedStatus.DOWNLOADED]),  # type: ignore[attr-defined]
            WantedItem.info_hash.is_not(None),  # type: ignore[union-attr]
        )
    )
    groups: dict[tuple[int, str], list[WantedItem]] = {}
    for row in result.scalars().all():
        assert row.info_hash is not None
        groups.setdefault((row.subscription_id, row.info_hash), []).append(row)
    return groups


async def _usable_downloaders(
    session: AsyncSession,
) -> list[tuple[DownloaderClient, DownloaderConfig]]:
    """全部可用（启用 + 连接验证通过）的下载器及其连接配置。"""
    repo = DownloaderRepository(session)
    rows = await repo.list_all()
    usable = []
    for row in rows:
        if not row.enabled or row.status != ConfigStatus.ACTIVE:
            continue
        usable.append(
            (
                row,
                DownloaderConfig(
                    type=row.client_type.value,
                    url=row.url,
                    username=row.username,
                    password=repo.decrypted_password(row),
                ),
            )
        )
    return usable


async def _query_torrent(
    info_hash: str, downloaders: list[tuple[DownloaderClient, DownloaderConfig]]
) -> TorrentStatus | None:
    """在全部可用下载器中查找种子（先到先得；单台故障不影响其余）。"""
    for row, config in downloaders:
        adapter = create_downloader(config)
        try:
            status = await adapter.get_torrent(info_hash)
        except Exception as exc:  # noqa: BLE001 -- 单台不可达降级继续
            logger.warning("查询下载器「%s」失败：%s", row.name, exc)
            continue
        finally:
            await adapter.close()
        if status is not None:
            return status
    return None


async def _process_group(
    subscription_id: int,
    info_hash: str,
    downloaders: list[tuple[DownloaderClient, DownloaderConfig]],
) -> None:
    status = await _query_torrent(info_hash, downloaders)
    db = get_database()
    async with db.session() as session:
        # 组内工单在会话内重取，避免跨会话对象失效
        rows = list(
            (
                await session.execute(
                    select(WantedItem).where(
                        WantedItem.subscription_id == subscription_id,
                        WantedItem.info_hash == info_hash,
                        WantedItem.status.in_(  # type: ignore[attr-defined]
                            [WantedStatus.GRABBED, WantedStatus.DOWNLOADED]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        subscription = await session.get(Subscription, subscription_id)
        if subscription is None:
            return
        item = await session.get(MediaItem, subscription.media_item_id)
        assert item is not None  # 外键保证
        repo = SubscriptionRepository(session)

        if status is None:
            await _handle_missing(session, repo, subscription, item, rows, info_hash)
            return
        if not status.completed:
            logger.debug(
                "《%s》的种子 %s 下载中：%.1f%%", item.title, info_hash, status.progress * 100
            )
            return

        # ① grabbed → downloaded（首次确认完成时记一条活动）
        newly_downloaded = [w for w in rows if w.status == WantedStatus.GRABBED]
        if newly_downloaded:
            now = utcnow()
            for w in newly_downloaded:
                await session.execute(
                    update(WantedItem)
                    .where(WantedItem.id == w.id, WantedItem.status == WantedStatus.GRABBED)
                    .values(status=WantedStatus.DOWNLOADED, downloaded_at=now, updated_at=now)
                )
            await session.commit()
            await repo.add_activity(
                SubscriptionActivity(
                    subscription_id=subscription_id,
                    wanted_item_id=rows[0].id,
                    type=ActivityType.DOWNLOADED,
                    message=f"下载完成：「{status.name}」，开始整理入库",
                    payload={"info_hash": info_hash},
                )
            )
            logger.info("《%s》的种子已下载完成：%s", item.title, status.name)

        # ② downloaded → imported（整理器；失败退避重试）
        await _try_import(session, repo, subscription, item, rows, info_hash, status)


async def _handle_missing(
    session: AsyncSession,
    repo: SubscriptionRepository,
    subscription: Subscription,
    item: MediaItem,
    rows: list[WantedItem],
    info_hash: str,
) -> None:
    """种子从下载器消失：退回 wanted 冷却后重新找资源。"""
    now = utcnow()
    from datetime import timedelta

    retry_at = now + timedelta(minutes=_MISSING_RETRY_MINUTES)
    for w in rows:
        await session.execute(
            update(WantedItem)
            .where(WantedItem.id == w.id)
            .values(
                status=WantedStatus.WANTED,
                info_hash=None,
                grabbed_at=None,
                downloaded_at=None,
                next_search_at=retry_at,
                updated_at=now,
            )
        )
    await session.commit()
    _import_backoff.pop(info_hash, None)
    assert subscription.id is not None
    await repo.add_activity(
        SubscriptionActivity(
            subscription_id=subscription.id,
            wanted_item_id=rows[0].id,
            type=ActivityType.DISPATCH_FAILED,
            message=(
                f"投递的种子已不在下载器中（可能被手动删除），"
                f"{_MISSING_RETRY_MINUTES} 分钟后重新寻找资源"
            ),
            payload={"info_hash": info_hash, "reason": "torrent_missing"},
        )
    )
    logger.warning("《%s》的种子 %s 已不在下载器中，工单退回队列", item.title, info_hash)


async def _try_import(
    session: AsyncSession,
    repo: SubscriptionRepository,
    subscription: Subscription,
    item: MediaItem,
    rows: list[WantedItem],
    info_hash: str,
    status: TorrentStatus,
) -> None:
    # 退避窗口内不重试（失败活动已记过，避免刷屏）
    entry = _import_backoff.get(info_hash)
    if entry and time.monotonic() < entry[0]:
        return
    assert subscription.id is not None
    try:
        outcome = await import_completed_torrent(
            session,
            subscription=subscription,
            item=item,
            wanted_rows=rows,
            status=status,
        )
    except LibraryImportError as exc:
        delay = min(entry[1] * 2 if entry else _BACKOFF_INITIAL, _BACKOFF_MAX)
        _import_backoff[info_hash] = (time.monotonic() + delay, delay)
        await repo.add_activity(
            SubscriptionActivity(
                subscription_id=subscription.id,
                wanted_item_id=rows[0].id,
                type=ActivityType.IMPORT_FAILED,
                message=f"整理入库失败：{exc}；约 {int(delay // 60)} 分钟后重试",
                payload={"info_hash": info_hash},
            )
        )
        logger.warning("《%s》整理入库失败：%s", item.title, exc)
        return

    _import_backoff.pop(info_hash, None)
    await _finalize_import(session, repo, subscription, item, rows, info_hash, outcome)


async def _finalize_import(
    session: AsyncSession,
    repo: SubscriptionRepository,
    subscription: Subscription,
    item: MediaItem,
    rows: list[WantedItem],
    info_hash: str,
    outcome: ImportOutcome,
) -> None:
    """标记工单 imported + 记时间线活动 + 派生重算。"""
    from movieclaw_api.services.subscription_matching import _units_text

    now = utcnow()
    covered = [w for w in rows if (w.season_number, w.episode_number) in outcome.imported_units]
    for w in covered:
        await session.execute(
            update(WantedItem)
            .where(WantedItem.id == w.id)
            .values(status=WantedStatus.IMPORTED, imported_at=now, updated_at=now)
        )
    await session.commit()

    uncovered = [w for w in rows if w not in covered]
    message = (
        f"已入库{_units_text(covered) if covered else ''} 到「{outcome.library_name}」"
        f"：{outcome.target_paths[0] if outcome.target_paths else ''}"
        + (f" 等 {len(outcome.target_paths)} 个文件" if len(outcome.target_paths) > 1 else "")
    )
    if outcome.skipped:
        message += f"；{len(outcome.skipped)} 个文件未能解析集号，留在下载目录"
    if uncovered:
        message += f"；{_units_text(uncovered)}在种子中未找到对应文件"
    assert subscription.id is not None
    await repo.add_activity(
        SubscriptionActivity(
            subscription_id=subscription.id,
            wanted_item_id=rows[0].id,
            type=ActivityType.IMPORTED,
            message=message,
            payload={
                "info_hash": info_hash,
                "library": outcome.library_name,
                "files": outcome.target_paths,
                "units": sorted(outcome.imported_units),
                "skipped": outcome.skipped,
            },
        )
    )
    await recompute_subscription_status(session, subscription, item)
    logger.info(
        "《%s》%s 已入库「%s」（%d 个文件）",
        item.title,
        _units_text(covered) if covered else "",
        outcome.library_name,
        len(outcome.target_paths),
    )
    # L4：通知媒体服务器刷新（未配置为 no-op；失败只告警不影响入库）
    from movieclaw_api.services.media_server_notify import notify_media_server_refresh

    await notify_media_server_refresh()
