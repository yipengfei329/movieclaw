"""投递（F5）：选定的候选 → 认领工单 → （取种 → 提交下载器）→ 台账与状态推进。

幂等三层防线的第一层在这里：**条件更新认领**——被动匹配与主动搜索并发命中
同一工单时，数据库保证只有一个赢家（docs/design/subscription-p4.md 第 5/7 节）。

真实投递（2026-07-24 起默认）：取种 → 保存目录过下载器路径映射翻译 →
提交默认下载器（编排收口 torrent_submit）。``SUBSCRIPTION_DISPATCH_DRY_RUN``
设为 true 可切回模拟投递（短路取种与提交、纯日志、照常推进状态机，
活动标注"模拟投递"），供调试匹配规则用。
"""

from __future__ import annotations

import logging

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

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

    # 入库目标：订阅指定的库（缺省该类型默认库）→ 条目目录 = 主根/标题 (年份)。
    # 投递目录三级兜底（与手动下载同构，保证订阅的下载永远有入库归宿）：
    # ① 库有监听导入规则 → 规则源目录（分离布局：下载区继续做种，完成后
    #    监听导入按 info_hash 认领身份硬链/复制进库）；
    # ② 无规则 → 库推导的条目目录（原地入库：直接下载进库根，库扫描实时
    #    入账，扫描的完整性检测保证半成品不入账）；
    # ③ 没有可用库/库无根路径 → 下载器默认目录（不会自动入库，文案写实）。
    # 完成后的搬运/入账仍由监听导入或库扫描接管，工单由库存对账关闭——
    # 订阅不亲自跟踪下载，但投递必须把种子送到那两个机制看得见的地方。
    from movieclaw_api.services.library_config import (
        LibraryConfigService,
        derive_save_path,
    )

    library = await LibraryConfigService(session).resolve_for_subscription(
        subscription.library_id, subscription.kind
    )
    save_path = derive_save_path(library, title=item.title, year=item.year) if library else None
    from movieclaw_api.services.import_watch_config import resolve_dispatch_dir

    rule_dir = await resolve_dispatch_dir(session, library.id if library else None)
    dispatch_dir = rule_dir or save_path
    # entry_level = 投递目录就是库内条目目录（②）：可以安全锚定副标题线索，
    # 帮扫描器收敛拼音命名的种子内容（监听目录/默认目录锚线索会波及无关内容）
    entry_level = rule_dir is None and save_path is not None
    if library is not None and rule_dir is not None:
        target_text = (
            f"；已投递到监听导入目录，下载完成后将整理入库到「{library.name}」：{save_path}"
        )
    elif library is not None and save_path is not None:
        target_text = f"；将直接下载到「{library.name}」库内目录：{save_path}，完成后自动入账"
    elif library is not None:
        target_text = f"；媒体库「{library.name}」未配置根路径，将落至下载器默认目录，不会自动入库"
    else:
        target_text = "；未配置媒体库，下载完成后不会自动整理入库"

    if not dry_run:
        try:
            submit_result = await _submit_real(
                session,
                candidate,
                save_path=dispatch_dir,
                subtitle=candidate.subtitle if entry_level else None,
            )
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
                "dispatch_dir": dispatch_dir,
            },
        )
    )
    await recompute_subscription_status(session, subscription, item)
    return True


async def preview_dispatch_route(
    session: AsyncSession, *, kind: str, library_id: int | None
) -> dict:
    """预演一次投递的路由结论（订阅弹窗/下载弹窗的预检数据源）。

    与 dispatch() 的三级兜底同源：监听规则源目录 → 库主根（条目目录的
    基底）→ 下载器默认目录；再叠加 submit_torrent 的映射覆盖守门判定。
    只读不投，返回结构化结论让前端在**订阅那一刻**就把问题亮给用户，
    而不是等投递失败/落点告警才发现。

    返回字段：mode（watch/inplace/downloader_default）、path（movieclaw
    视角的投递基底目录）、library_name、downloader_name、ok、warning
    （不 ok 时的中文指引）。
    """
    from movieclaw_api.services.import_watch_config import resolve_dispatch_dir
    from movieclaw_api.services.library_config import LibraryConfigService
    from movieclaw_api.services.torrent_submit import mapping_covers
    from movieclaw_db.models.downloader_client import DownloaderClient
    from movieclaw_db.models.site_credential import ConfigStatus

    library = await LibraryConfigService(session).resolve_for_subscription(library_id, kind)
    rule_dir = await resolve_dispatch_dir(session, library.id if library else None)
    root = library.primary_root if library else None
    base = rule_dir or root

    result = await session.execute(
        select(DownloaderClient).where(
            DownloaderClient.is_default.is_(True),  # type: ignore[attr-defined]
            DownloaderClient.enabled.is_(True),  # type: ignore[attr-defined]
            DownloaderClient.status == ConfigStatus.ACTIVE,
        )
    )
    downloader = result.scalars().first()

    mode = "watch" if rule_dir else ("inplace" if root else "downloader_default")
    ok = True
    warning: str | None = None
    if downloader is None:
        ok = False
        warning = "没有可用的默认下载器，请先在「设置 → 下载器」添加并确保连接测试通过"
    elif base is None:
        ok = False
        warning = (
            "没有可用的媒体库（或库未配置根路径），下载会落到下载器默认目录且不会自动入库"
        )
    elif downloader.path_mappings and not mapping_covers(base, downloader.path_mappings):
        ok = False
        warning = (
            f"目录 {base} 不在下载器「{downloader.name}」的路径映射覆盖范围内，"
            "届时投递会被拒绝——请为该目录补一条路径映射，或为这个库配置监听导入规则"
        )
    return {
        "mode": mode,
        "path": base,
        "library_name": library.name if library else None,
        "downloader_name": downloader.name if downloader else None,
        "ok": ok,
        "warning": warning,
    }


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


async def _submit_real(
    session: AsyncSession,
    candidate: TorrentCandidate,
    *,
    save_path: str | None = None,
    subtitle: str | None = None,
):
    """真实投递：委托公共编排（站点取种 → 默认下载器提交，幂等判重）。

    save_path 按三级兜底解析（监听规则源目录 / 库条目目录 / None 退下载器
    默认目录），完成后的搬运/入账由监听导入或库扫描接管，库存对账关闭工单。
    subtitle 仅在投递目录为**条目级**时传入（download_hint 线索只能锚条目
    目录——锚到监听目录/默认目录会波及目录下全部内容）。
    """
    from movieclaw_api.services.torrent_submit import submit_torrent

    result, _row = await submit_torrent(
        session,
        site_id=candidate.site_id,
        download_url=candidate.download_url,
        tags=["movieclaw-sub"],
        save_path=save_path,
        subtitle=subtitle,
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
