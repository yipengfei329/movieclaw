"""库存对账：library_file 的在位单元关闭对应的订阅工单（订阅止于投递的另一半）。

订阅的目标本来就是"库里有"——工单的完成状态从**库存**推导，而不是从
管线事件推导：任何路径（监听导入搬运、库扫描原地入账、人工认领）让某个
(条目, 季, 集) 单元出现在库里，对应的开放工单即关闭、订阅派生状态重算、
时间线补记"已入库"。这让入库引擎（扫描/监听导入）无需知道订阅的存在，
订阅也无需亲自跟踪下载与搬运。

调用点：library_scan 识别入账后、library_ingest 搬运入库后、待识别
人工认领后。函数幂等：没有可关闭的工单时是纯查询。
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

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
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository

logger = logging.getLogger("movieclaw_api.wanted_fulfillment")


async def close_fulfilled_wanted(session: AsyncSession, media_item_id: int) -> int:
    """把某条目已在库的单元对应的开放工单标记为已入库。返回关闭数。"""
    owned = await LibraryFileRepository(session).owned_units(media_item_id)
    if not owned:
        return 0
    rows = list(
        (
            await session.execute(
                select(WantedItem).where(
                    WantedItem.media_item_id == media_item_id,
                    WantedItem.status != WantedStatus.IMPORTED,  # type: ignore[arg-type]
                )
            )
        )
        .scalars()
        .all()
    )
    fulfilled = [w for w in rows if (w.season_number, w.episode_number) in owned]
    if not fulfilled:
        return 0

    now = utcnow()
    by_subscription: dict[int, list[WantedItem]] = {}
    for wanted in fulfilled:
        wanted.status = WantedStatus.IMPORTED
        wanted.imported_at = now
        wanted.updated_at = now
        by_subscription.setdefault(wanted.subscription_id, []).append(wanted)
    await session.commit()

    # 时间线与派生状态：逐订阅补记（对账可能一次关闭多个订阅的工单）
    from movieclaw_api.services.subscription import recompute_subscription_status
    from movieclaw_api.services.subscription_matching import _units_text

    item = await session.get(MediaItem, media_item_id)
    repo = SubscriptionRepository(session)
    for subscription_id, wanted_rows in by_subscription.items():
        subscription = await session.get(Subscription, subscription_id)
        if subscription is None or item is None:
            continue
        await repo.add_activity(
            SubscriptionActivity(
                subscription_id=subscription_id,
                wanted_item_id=wanted_rows[0].id,
                type=ActivityType.IMPORTED,
                message=f"{_units_text(wanted_rows)}已入库（媒体库对账确认）",
                payload={"units": [[w.season_number, w.episode_number] for w in wanted_rows]},
            )
        )
        await recompute_subscription_status(session, subscription, item)
    logger.info("库存对账：条目 #%s 关闭了 %d 个工单", media_item_id, len(fulfilled))

    # L4：通知媒体服务器刷新（未配置为 no-op；失败只告警）
    from movieclaw_api.services.media_server_notify import notify_media_server_refresh

    await notify_media_server_refresh()
    return len(fulfilled)
