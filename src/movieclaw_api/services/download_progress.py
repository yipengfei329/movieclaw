"""投递救援巡检：照看订阅在途投递的种子，只救援、不搬运。

订阅止于投递（架构定稿）：投递记下 info_hash 后，下载完成的搬运由
监听导入（按 info_hash 认领身份）或库扫描（原地入账）完成，工单的
完成状态由库存对账关闭（wanted_fulfillment）。本任务只剩投递方
自己的责任——**照看投递结果的死活**：

- 种子在所有可用下载器中都查不到（被手动删除）→ 工单退回 wanted
  短冷却后重新找资源；
- 种子长期（STALLED_REQUEUE_DAYS）未完成 → 视为卡死，退回重新找
  资源（旧种子若之后完成，库存对账照样关闭工单，不冲突）；
- 其余情况（下载中/已完成待入库）不做任何事。

失败语义沿用：每组独立处理，单组失败不拖垮整轮，中文活动可回放。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

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

# 巡检节奏：救援不追求秒级——5 分钟内发现"种子被删"足够灵敏
PROGRESS_TICK_SECONDS = 300

# 种子被手动删除后工单退回 wanted 的冷却（给用户留出"删错了重新添加"的窗口）
_MISSING_RETRY_MINUTES = 30

# 卡死判定：投递后超过该天数仍未下载完成，退回重新找资源（大体积慢速种子
# 也少有超过一周的；判错的代价只是多找一个候选，旧种子完成后照样入库）
STALLED_REQUEUE_DAYS = 7

_tick_lock = asyncio.Lock()

# 在途状态：GRABBED 为主；DOWNLOADED 是旧版管线的遗留中间态（新架构不再
# 写入），存量行按同样语义照看直至库存对账关闭
_IN_FLIGHT = (WantedStatus.GRABBED, WantedStatus.DOWNLOADED)


@register_task(
    "check_download_progress",
    title="投递救援巡检",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=PROGRESS_TICK_SECONDS,
    description=(
        "照看订阅在途投递的种子：被手动删除或长期卡死的工单退回重新找资源。"
        "下载完成后的入库由监听导入/库扫描完成，工单由库存对账关闭。"
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
            logger.warning("有 %d 个在途种子等待照看，但没有可用的下载器", len(groups))
            return
        for subscription_id, info_hash in groups:
            try:
                await _rescue_group(subscription_id, info_hash, downloaders)
            except Exception:  # noqa: BLE001 -- 单组失败不拖垮整轮
                logger.exception("种子 %s（订阅 #%s）的救援巡检失败", info_hash, subscription_id)


async def _pipeline_groups(
    session: AsyncSession,
) -> dict[tuple[int, str], list[WantedItem]]:
    """在途工单，按（订阅, 种子）分组。"""
    result = await session.execute(
        select(WantedItem).where(
            WantedItem.status.in_(_IN_FLIGHT),  # type: ignore[attr-defined]
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


async def _rescue_group(
    subscription_id: int,
    info_hash: str,
    downloaders: list[tuple[DownloaderClient, DownloaderConfig]],
) -> None:
    status = await _query_torrent(info_hash, downloaders)
    db = get_database()
    async with db.session() as session:
        # 组内工单在会话内重取（库存对账可能刚关闭了其中一部分）
        rows = list(
            (
                await session.execute(
                    select(WantedItem).where(
                        WantedItem.subscription_id == subscription_id,
                        WantedItem.info_hash == info_hash,
                        WantedItem.status.in_(_IN_FLIGHT),  # type: ignore[attr-defined]
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
            await _requeue(
                session,
                repo,
                item,
                rows,
                info_hash,
                message=(
                    f"投递的种子已不在下载器中（可能被手动删除），"
                    f"{_MISSING_RETRY_MINUTES} 分钟后重新寻找资源"
                ),
                reason="torrent_missing",
            )
            return

        if not status.completed and _stalled(rows):
            await _requeue(
                session,
                repo,
                item,
                rows,
                info_hash,
                message=(
                    f"「{status.name}」投递超过 {STALLED_REQUEUE_DAYS} 天仍未下载完成，"
                    "退回重新寻找资源（原种子保留在下载器中，完成后仍会自动入库）"
                ),
                reason="stalled",
            )
            return

        # 下载中/已完成待入库：不做任何事——完成后的搬运由监听导入/库扫描
        # 负责，工单由库存对账关闭
        logger.debug(
            "《%s》的种子 %s：%s",
            item.title,
            info_hash,
            "已完成待入库" if status.completed else "下载中",
        )


def _stalled(rows: list[WantedItem]) -> bool:
    """整组工单是否已卡死：以最近一次状态推进的时间为基准。"""
    threshold = utcnow() - timedelta(days=STALLED_REQUEUE_DAYS)
    return all((w.grabbed_at or w.updated_at) < threshold for w in rows)


async def _requeue(
    session: AsyncSession,
    repo: SubscriptionRepository,
    item: MediaItem,
    rows: list[WantedItem],
    info_hash: str,
    *,
    message: str,
    reason: str,
) -> None:
    """把一组在途工单退回 wanted：冷却后重新找资源，记中文活动。"""
    now = utcnow()
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
    await repo.add_activity(
        SubscriptionActivity(
            subscription_id=rows[0].subscription_id,
            wanted_item_id=rows[0].id,
            type=ActivityType.DISPATCH_FAILED,
            message=message,
            payload={"info_hash": info_hash, "reason": reason},
        )
    )
    logger.warning("《%s》的种子 %s 已退回队列：%s", item.title, info_hash, reason)
