"""被动匹配（F2）：新入库种子 × 活跃缺口，水位驱动。

触发有两处（docs/design/subscription-p4.md 第 3 节）：
1. ``sync_site_torrents`` 尾部直调（同进程零延迟）；
2. 低频兜底任务（进程重启期间的漏网 + sync 异常中断后的补扫）。

水位语义：``app_setting`` 里存最后处理过的 ``site_torrent.id``。**首次运行把
水位初始化到当前最大 id**——历史缓存不参与匹配，这是"本地缓存只用来追新，
补旧永远真实搜索"铁律的实现落点：缓存对旧内容覆盖不完整，偶然命中给出的
是残缺候选集。
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import func
from sqlmodel import select

from movieclaw_api.services.subscription_matching import (
    MATCH_BATCH_SIZE,
    evaluate_and_dispatch,
    load_match_context,
)
from movieclaw_db.engine import get_database
from movieclaw_db.models import SiteTorrent
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.repositories import SettingRepository
from movieclaw_scheduler import register_task

logger = logging.getLogger("movieclaw_api.torrent_matcher")

_WATERMARK_NAMESPACE = "subscription_match_watermark"

# 单实例部署假设下，进程内锁足以避免 sync 尾调与兜底任务并发推进水位
_lock = asyncio.Lock()


async def process_new_torrents() -> None:
    """扫描水位之后的新种子，喂给共享评估管道，批处理直到追平。

    背景任务语义：绝不向外抛异常（sync 尾调时不能影响同步主流程）。
    """
    try:
        async with _lock:
            await _process_locked()
    except Exception:  # noqa: BLE001 -- 背景匹配失败只记日志，等下一轮
        logger.exception("被动匹配执行失败，等待下一轮触发")


async def _process_locked() -> None:
    db = get_database()
    while True:
        async with db.session() as session:
            settings_repo = SettingRepository(session)
            watermark = await _read_watermark(settings_repo)
            if watermark is None:
                # 首次运行：水位落到当前最大 id，历史缓存不参与匹配（见模块注释）
                result = await session.execute(select(func.max(SiteTorrent.id)))
                latest = int(result.scalar_one() or 0)
                await _write_watermark(settings_repo, latest)
                logger.info("被动匹配水位初始化：从 site_torrent #%d 之后开始跟随", latest)
                return

            result = await session.execute(
                select(SiteTorrent)
                .where(SiteTorrent.id > watermark)  # type: ignore[arg-type]
                .order_by(SiteTorrent.id)  # type: ignore[arg-type]
                .limit(MATCH_BATCH_SIZE)
            )
            rows = list(result.scalars().all())
            if not rows:
                return

            # 没有任何缺口时只推进水位，不做逐种子评估
            contexts = await load_match_context(session)
            if contexts:
                await evaluate_and_dispatch(session, rows, source="被动匹配")
            await _write_watermark(settings_repo, rows[-1].id or watermark)

        if len(rows) < MATCH_BATCH_SIZE:
            return  # 已追平


async def _read_watermark(repo: SettingRepository) -> int | None:
    row = await repo.get(_WATERMARK_NAMESPACE)
    if row is None:
        return None
    try:
        return int(json.loads(row.value_json)["last_id"])
    except (ValueError, KeyError, TypeError):
        logger.warning("被动匹配水位记录损坏，按首次运行处理")
        return None


async def _write_watermark(repo: SettingRepository, last_id: int) -> None:
    await repo.upsert(_WATERMARK_NAMESPACE, json.dumps({"last_id": last_id}))


@register_task(
    "match_new_torrents",
    title="订阅被动匹配（兜底扫描）",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=3600,
    description=(
        "低频兜底：扫描种子索引中水位之后的新种子并匹配订阅缺口。"
        "主触发在站点同步任务尾部（零延迟），本任务只兜进程重启/同步异常的漏网。"
    ),
)
async def match_new_torrents_task() -> None:
    await process_new_torrents()
