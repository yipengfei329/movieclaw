"""元数据刷新（F3）：让期望集合 E 随现实生长——"只追未来"的发动机。

每 tick 处理少量到期条目：拉 TMDB 最新档案 → diff 季集 → 给订阅补新工单、
同步改档期、重排下次刷新。分档间隔见 ``_next_refresh_delay``
（docs/design/subscription-p4.md 第 6 节）。
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.subscription import (
    ExpectedUnit,
    expected_units,
    recompute_subscription_status,
    schedule_for,
)
from movieclaw_api.services.subscription_matching import REFRESH_PER_TICK
from movieclaw_db.engine import get_database
from movieclaw_db.models import (
    ActivityType,
    MediaItem,
    MediaSeason,
    Subscription,
    SubscriptionActivity,
    WantedItem,
    WantedStatus,
    utcnow,
)
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.repositories import MediaItemRepository, SubscriptionRepository
from movieclaw_media.library import fetch_media_profile
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbError, TmdbNotConfiguredError
from movieclaw_scheduler.registry import register_task

logger = logging.getLogger("movieclaw_api.media_refresh")

_RETRY_DELAY = timedelta(hours=1)  # 单条目刷新失败的重试间隔
_ENDED = frozenset({"Ended", "Canceled"})


@register_task(
    "refresh_media_metadata",
    title="订阅元数据刷新",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=900,
    description=(
        "定期从 TMDB 刷新已建档条目的季集与状态：发现新集给追新订阅补工单、"
        "同步改档期。在播剧 8 小时一刷，完结/无订阅条目低频保鲜。"
    ),
)
async def refresh_media_metadata() -> None:
    db = get_database()
    async with db.session() as session:
        result = await session.execute(
            select(MediaItem)
            .where(
                (MediaItem.next_refresh_at.is_(None))  # type: ignore[union-attr]
                | (MediaItem.next_refresh_at <= utcnow())  # type: ignore[operator]
            )
            .order_by(MediaItem.next_refresh_at)  # type: ignore[arg-type]
            .limit(REFRESH_PER_TICK)
        )
        due = list(result.scalars().all())
    if not due:
        return

    try:
        client = get_tmdb_client()
    except TmdbNotConfiguredError:
        logger.warning("未配置 TMDB API Key，元数据刷新跳过本轮")
        return

    for item in due:
        try:
            await _refresh_one(client, item.id)  # type: ignore[arg-type]
        except TmdbError as exc:
            await _postpone(item.id, reason=str(exc))  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 -- 单条目失败不拖垮整轮
            logger.exception("条目 #%s 元数据刷新失败", item.id)
            await _postpone(item.id, reason="未知错误，详见日志")  # type: ignore[arg-type]


async def _refresh_one(client, media_item_id: int) -> None:
    db = get_database()
    async with db.session() as session:
        media_repo = MediaItemRepository(session)
        item = await session.get(MediaItem, media_item_id)
        if item is None:
            return
        kind = MediaKind(item.kind)
        profile = await fetch_media_profile(client, kind, item.tmdb_id)

        # -- 条目字段与别名合并（别名只增不减：来源信息不丢）------------------
        merged_aliases = list(item.aliases)
        seen = set(merged_aliases)
        for alias in profile.aliases:
            if alias not in seen:
                merged_aliases.append(alias)
                seen.add(alias)
        item.title = profile.title or item.title
        item.original_title = profile.original_title or item.original_title
        item.year = profile.year or item.year
        item.status = profile.status or item.status
        item.poster_path = profile.poster_path or item.poster_path
        item.backdrop_path = profile.backdrop_path or item.backdrop_path
        item.aliases = merged_aliases
        item.imdb_id = item.imdb_id or profile.imdb_id

        # -- 季集 upsert，记录本次新出现的集 ----------------------------------
        season_rows = await media_repo.list_seasons(media_item_id)
        existing_seasons = {s.season_number: s for s in season_rows}
        known_keys: set[tuple[int, int]] = {
            (s.season_number, e["episode_number"])
            for s in existing_seasons.values()
            for e in s.episodes
            if e.get("episode_number") is not None
        }
        for season_profile in profile.seasons:
            episodes_json = [e.model_dump() for e in season_profile.episodes]
            row = existing_seasons.get(season_profile.season_number)
            if row is None:
                row = MediaSeason(
                    media_item_id=media_item_id,
                    season_number=season_profile.season_number,
                )
            row.name = season_profile.name
            row.air_date = season_profile.air_date
            row.episode_count = season_profile.episode_count
            row.episodes = episodes_json
            row.updated_at = utcnow()
            session.add(row)

        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.media_item_id == media_item_id)
            )
        ).scalar_one_or_none()

        item.metadata_refreshed_at = utcnow()
        item.next_refresh_at = utcnow() + _next_refresh_delay(item, subscription)
        session.add(item)
        await session.commit()

        if subscription is None or kind is MediaKind.MOVIE:
            return

        # -- 工单生长与档期同步（对 paused 订阅也生长：暂停只挡匹配与搜索）----
        seasons = await media_repo.list_seasons(media_item_id)
        expected = expected_units(
            kind, seasons, list(subscription.selected_seasons), subscription.follow_future
        )
        await _grow_and_sync(session, subscription, item, expected, known_keys)


async def _grow_and_sync(
    session: AsyncSession,
    subscription: Subscription,
    item: MediaItem,
    expected: list[ExpectedUnit],
    known_keys: set[tuple[int, int]],
) -> None:
    repo = SubscriptionRepository(session)
    assert subscription.id is not None
    existing = {
        (w.season_number, w.episode_number): w for w in await repo.list_wanted(subscription.id)
    }
    # 库存 H：库里已有的单元不再补单（E−H 用真实的 H，媒体库 L3 联通）
    from movieclaw_db.repositories.library_file_repo import LibraryFileRepository

    owned = await LibraryFileRepository(session).owned_units(subscription.media_item_id)

    to_add: list[WantedItem] = []
    for unit in expected:
        key = (unit.season_number, unit.episode_number)
        wanted = existing.get(key)
        if wanted is None:
            if key in known_keys:
                # 早已存在于季集数据、但不在工单里的单元属于历史 diff 结果
                # （如追新期间被移除），不因刷新复活——只有**新出现的集**才补
                continue
            if key in owned:
                continue  # 库里已经有这一集，无需追
            next_search, priority = schedule_for(subscription.kind, unit)
            to_add.append(
                WantedItem(
                    subscription_id=subscription.id,
                    media_item_id=subscription.media_item_id,
                    season_number=unit.season_number,
                    episode_number=unit.episode_number,
                    status=WantedStatus.WANTED,
                    air_date=unit.air_date,
                    priority=priority,
                    next_search_at=next_search,
                )
            )
            continue
        # 档期同步：未定档→定档回填调度；已在退避中的（attempts>0）不打扰
        if (
            wanted.status == WantedStatus.WANTED
            and wanted.air_date != unit.air_date
            and wanted.search_attempts == 0
        ):
            next_search, priority = schedule_for(subscription.kind, unit)
            wanted.air_date = unit.air_date
            wanted.next_search_at = next_search
            wanted.priority = priority
            wanted.updated_at = utcnow()
            session.add(wanted)
    if to_add:
        await repo.add_wanted(to_add)
        first = to_add[0]
        label = f"S{first.season_number:02d}E{first.episode_number:02d}"
        await repo.add_activity(
            SubscriptionActivity(
                subscription_id=subscription.id,
                type=ActivityType.WANTED_ADDED,
                message=(f"元数据刷新发现 {len(to_add)} 个新集（{label} 起），已加入追踪"),
                payload={"units": [[w.season_number, w.episode_number] for w in to_add]},
            )
        )
        logger.info("《%s》发现 %d 个新集，已补工单", item.title, len(to_add))
    else:
        await session.commit()  # 只有档期同步时也要落盘

    await recompute_subscription_status(session, subscription, item)


def _next_refresh_delay(item: MediaItem, subscription: Subscription | None) -> timedelta:
    """刷新分档（docs/design/subscription-p4.md 第 6 节）。"""
    if subscription is None:
        return timedelta(days=30)  # 无订阅：低频保鲜别名，防条目腐烂
    if item.kind == MediaKind.TV.value and (item.status or "") not in _ENDED:
        return timedelta(hours=8)  # 在播/未完结剧：新集的发动机
    if item.kind == MediaKind.MOVIE.value and (item.status or "") != "Released":
        return timedelta(hours=24)  # 未上映电影：等定档/上映
    return timedelta(days=7)  # 已完结/已上映：低频


async def _postpone(media_item_id: int, *, reason: str) -> None:
    db = get_database()
    async with db.session() as session:
        item = await session.get(MediaItem, media_item_id)
        if item is None:
            return
        item.next_refresh_at = utcnow() + _RETRY_DELAY
        session.add(item)
        await session.commit()
    logger.warning("条目 #%s 刷新失败（%s），%s 后重试", media_item_id, reason, _RETRY_DELAY)
