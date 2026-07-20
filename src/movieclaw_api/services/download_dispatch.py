"""投递（F5）：选定的候选 → 认领工单 → （取种 → 提交下载器）→ 台账与状态推进。

幂等三层防线的第一层在这里：**条件更新认领**——被动匹配与主动搜索并发命中
同一工单时，数据库保证只有一个赢家（docs/design/subscription-p4.md 第 5/7 节）。

模拟投递（已确认决策）：``SUBSCRIPTION_DISPATCH_DRY_RUN``（默认开）短路
取种与提交，打完整中文日志、照常推进状态机，活动标注"模拟投递"。
真实投递路径已就位，关掉开关即切换，代码路径不变。
"""

from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.core.config import get_settings
from movieclaw_db.models import (
    ActivityType,
    MediaItem,
    Subscription,
    SubscriptionActivity,
    WantedItem,
    WantedStatus,
    utcnow,
)
from movieclaw_db.repositories import SubscriptionRepository
from movieclaw_matcher import RuleVerdict, TorrentCandidate

logger = logging.getLogger("movieclaw_api.download_dispatch")


async def dispatch(
    session: AsyncSession,
    *,
    subscription: Subscription,
    item: MediaItem,
    wanted_rows: list[WantedItem],
    candidate: TorrentCandidate,
    verdict: RuleVerdict,
    source: str,
) -> bool:
    """把候选投递给下载器，满足给定的一批工单。返回是否有实际投递发生。"""
    from movieclaw_api.services.subscription import recompute_subscription_status
    from movieclaw_api.services.subscription_matching import (
        DISPATCH_RETRY_DELAY,
        _units_text,
    )

    claimed = await _claim(session, wanted_rows)
    if not claimed:
        return False  # 全部被另一条路径抢先，本候选无事可做

    repo = SubscriptionRepository(session)
    assert subscription.id is not None
    dry_run = get_settings().subscription_dispatch_dry_run
    units_text = _units_text(claimed)
    spec_text = _describe(candidate)

    # 入库目标预告：订阅指定的库（缺省该类型默认库）→ 目标 = 主根/标题 (年份)。
    # L2 起下载本体落**下载器默认目录**（下载区），完成后由整理器硬链入库——
    # 这里只解析目标路径用于时间线展示。dry-run 同样解析，可预览最终归宿。
    from movieclaw_api.services.library_config import (
        LibraryConfigService,
        derive_save_path,
    )

    library = await LibraryConfigService(session).resolve_for_subscription(
        subscription.library_id, subscription.kind
    )
    save_path = derive_save_path(library, title=item.title, year=item.year) if library else None
    if library is not None and save_path is not None:
        target_text = f"；下载完成后将入库到「{library.name}」：{save_path}"
    else:
        target_text = "；未配置媒体库，下载完成后不会自动整理入库"

    if not dry_run:
        try:
            submit_result = await _submit_real(session, candidate)
        except Exception as exc:  # noqa: BLE001 -- 投递失败退回调度通道重试
            reason = f"{type(exc).__name__}: {exc}"
            await _rollback_claim(session, claimed, retry_delay=DISPATCH_RETRY_DELAY)
            await repo.add_activity(
                SubscriptionActivity(
                    subscription_id=subscription.id,
                    wanted_item_id=claimed[0].id,
                    type=ActivityType.DISPATCH_FAILED,
                    message=(
                        f"{units_text}投递失败：{reason}；已退回队列，"
                        f"约 {int(DISPATCH_RETRY_DELAY.total_seconds() // 60)} 分钟后重试"
                    ),
                    payload={
                        "site_id": candidate.site_id,
                        "torrent_id": candidate.torrent_id,
                        "source": source,
                    },
                )
            )
            logger.warning(
                "投递失败（%s）：《%s》%s ← %s/%s：%s",
                source,
                item.title,
                units_text,
                candidate.site_id,
                candidate.torrent_id,
                reason,
            )
            return False
        # 记录 infohash：完成轮询任务据此追踪下载进度并触发入库整理
        if submit_result.info_hash:
            now = utcnow()
            for wanted in claimed:
                await session.execute(
                    update(WantedItem)
                    .where(WantedItem.id == wanted.id)
                    .values(info_hash=submit_result.info_hash, updated_at=now)
                )
            await session.commit()

    mode = "【模拟投递】" if dry_run else ""
    logger.info(
        "%s已投递（%s）：《%s》%s ← %s 的「%s」（%s）",
        mode,
        source,
        item.title,
        units_text,
        candidate.site_id,
        candidate.title[:80],
        spec_text,
    )
    await repo.add_activity(
        SubscriptionActivity(
            subscription_id=subscription.id,
            wanted_item_id=claimed[0].id,
            type=ActivityType.GRABBED,
            message=(
                f"已投递{units_text}：来自 {candidate.site_id} 的"
                f"「{candidate.title[:60]}」（{spec_text}）"
                + target_text
                + ("——模拟投递，未真实提交下载器" if dry_run else "")
            ),
            payload={
                "site_id": candidate.site_id,
                "torrent_id": candidate.torrent_id,
                "score": verdict.score,
                "source": source,
                "dry_run": dry_run,
                "units": [[w.season_number, w.episode_number] for w in claimed],
                "library_id": library.id if library else None,
                "save_path": save_path,
            },
        )
    )
    await recompute_subscription_status(session, subscription, item)
    return True


async def _claim(session: AsyncSession, wanted_rows: list[WantedItem]) -> list[WantedItem]:
    """条件更新认领（防线①）：只把仍是 wanted 态的工单推进到 grabbed。

    逐条执行拿到精确的"谁被我认领了"；工单数量级小（整季包也就几十条），
    不值得为省几次 UPDATE 引入批量+回读的复杂度。
    """
    claimed: list[WantedItem] = []
    now = utcnow()
    for wanted in wanted_rows:
        result = await session.execute(
            update(WantedItem)
            .where(WantedItem.id == wanted.id, WantedItem.status == WantedStatus.WANTED)
            .values(status=WantedStatus.GRABBED, grabbed_at=now, updated_at=now)
        )
        if result.rowcount:
            claimed.append(wanted)
    await session.commit()
    return claimed


async def _rollback_claim(session: AsyncSession, claimed: list[WantedItem], *, retry_delay) -> None:
    """投递失败：认领回滚，退回调度通道（next_search_at 短冷却）择机重试。"""
    now = utcnow()
    for wanted in claimed:
        await session.execute(
            update(WantedItem)
            .where(WantedItem.id == wanted.id, WantedItem.status == WantedStatus.GRABBED)
            .values(
                status=WantedStatus.WANTED,
                grabbed_at=None,
                next_search_at=now + retry_delay,
                updated_at=now,
            )
        )
    await session.commit()


async def _submit_real(session: AsyncSession, candidate: TorrentCandidate):
    """真实投递：委托公共编排（站点取种 → 默认下载器提交，幂等判重）。

    下载本体落**下载器默认目录**（下载区继续做种），入库由整理器硬链完成
    （L2.4 语义切换）。返回 SubmitResult 供调用方记录 infohash。
    dry-run 关闭后才会走到这里。任何一步抛错由调用方统一回滚认领。
    """
    from movieclaw_api.services.torrent_submit import submit_torrent

    result, _row = await submit_torrent(
        session,
        site_id=candidate.site_id,
        download_url=candidate.download_url,
        tags=["movieclaw-sub"],
    )
    return result


def _describe(candidate: TorrentCandidate) -> str:
    """候选的一句话规格描述，进活动与日志。"""
    parts: list[str] = []
    if candidate.attrs.resolution:
        parts.append(candidate.attrs.resolution)
    if candidate.is_free is True:
        parts.append("free")
    if candidate.seeders is not None:
        parts.append(f"{candidate.seeders} 做种")
    return " · ".join(parts) if parts else "规格未知"
