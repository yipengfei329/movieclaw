"""订阅服务：期望集合 E 的定义、物化与调和入口（docs/design/subscription.md 第 2 节）。

核心算法只有两个，全部围绕 E：

- **初始化**：``_expected_units`` 按订阅参数展开期望单元，``_schedule_for`` 给每个
  单元写死调度语义（补旧=now / 追新=air_date+宽限 / 未定档=NULL）；
- **diff 重算**：修改订阅时新增缺的、删掉出域且未完成的，**已 grabbed 的永不回收**
  （不变量③：现实不可逆）。

订阅状态是派生值（不变量④）：``_recompute_status`` 随时可从工单集合重算，
paused 是用户显式操作、重算不碰它。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import BadRequestException, NotFoundException
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.rule_sets import RuleSetService
from movieclaw_db.models import (
    ActivityType,
    MediaItem,
    MediaSeason,
    Subscription,
    SubscriptionActivity,
    SubscriptionStatus,
    WantedItem,
    WantedStatus,
    utcnow,
)
from movieclaw_db.repositories import MediaItemRepository, SubscriptionRepository
from movieclaw_media.models import MediaKind

logger = logging.getLogger("movieclaw_api.subscription")

# 追新工单的漏抓宽限期：air_date + 该值 = 首个真实搜索到期时刻。
# 被动匹配通常在到点前满足工单；真到点仍缺的，worker 捞起即漏抓兜底。
FUTURE_GRACE = timedelta(hours=48)

# 剧集完结类 status：期望集合不再生长的判定输入之一
_ENDED_STATUSES = frozenset({"Ended", "Canceled"})


@dataclass(frozen=True)
class ExpectedUnit:
    """期望单元：E 集合的元素。电影是 (0, 0) 哨兵单元。"""

    season_number: int
    episode_number: int
    air_date: date | None


def expected_units(
    kind: MediaKind,
    seasons: list[MediaSeason],
    selected: list[int],
    follow_future: bool,
) -> list[ExpectedUnit]:
    """按 E 的定义展开期望单元（订阅创建/修改与元数据刷新共用）。

    E = 勾选季的全部已知集 ∪（follow_future ? 评估时刻之后播出的集 : ∅）

    追新的锚定取**评估时刻**而非订阅创建时刻：创建时二者等价；后来才打开
    追新开关时，不回补开关关闭期间播出的集（用户此刻的意图是"从现在起追"）。
    更早的历史集通过勾选季表达。特别季 0 仅显式勾选才纳入。
    """
    if kind is MediaKind.MOVIE:
        return [ExpectedUnit(0, 0, None)]

    today = utcnow().date()
    selected_set = set(selected)
    units: list[ExpectedUnit] = []
    for season in seasons:
        in_selected = season.season_number in selected_set
        if not in_selected and not follow_future:
            continue
        for episode in season.episodes:
            number = episode.get("episode_number")
            if number is None:
                continue
            air = _parse_date(episode.get("air_date"))
            if in_selected:
                units.append(ExpectedUnit(season.season_number, number, air))
            elif season.season_number != 0 and (air is None or air > today):
                # 追新贡献：未勾季里"尚未播出/未定档"的集；特别季不自动追
                units.append(ExpectedUnit(season.season_number, number, air))
    return units


def schedule_for(kind: str, unit: ExpectedUnit) -> tuple[datetime | None, int]:
    """三类调度语义（创建时写死）：补旧=now / 追新=air+宽限 / 未定档=NULL。

    电影没有播出日期概念，恒为补旧。追新工单给高优先级：新集是用户
    最急着要的，worker 排序时优先处理。
    """
    now = utcnow()
    if kind == MediaKind.MOVIE.value:
        return now, 0
    if unit.air_date is None:
        return None, 0  # 未定档：不可调度，元数据刷新定档时回填
    if unit.air_date <= now.date():
        return now, 0  # 补旧：立即排队真实搜索
    first_due = datetime.combine(unit.air_date, datetime.min.time()) + FUTURE_GRACE
    return first_due, 10  # 追新：被动匹配为主，到点即漏抓兜底


async def recompute_subscription_status(
    session: AsyncSession, subscription: Subscription, item: MediaItem
) -> None:
    """派生状态重算（不变量④）：completed ⟺ 无未满足工单 且 E 不再生长。

    paused 是用户显式状态，不碰。状态翻转本身也是活动（透明化）。
    注：P4 阶段"满足"= grabbed（已提交下载）；P5 接入下载完成确认后收紧。
    模块级函数：订阅服务与投递/刷新管线共用，不依赖 TMDB client。
    """
    if subscription.status == SubscriptionStatus.PAUSED:
        return
    assert subscription.id is not None
    repo = SubscriptionRepository(session)
    wanted = await repo.list_wanted(subscription.id)
    has_open = any(w.status == WantedStatus.WANTED for w in wanted)
    growing = (
        subscription.kind == MediaKind.TV.value
        and subscription.follow_future
        and (item.status or "") not in _ENDED_STATUSES
    )
    new_status = (
        SubscriptionStatus.COMPLETED
        if not has_open and not growing
        else SubscriptionStatus.ACTIVE
    )
    if subscription.status == new_status:
        return
    subscription.status = new_status
    await repo.save(subscription)
    if new_status == SubscriptionStatus.COMPLETED:
        message = "订阅已收齐：期望的内容都已安排完毕，且暂无会新增的内容"
        activity_type = ActivityType.COMPLETED
    else:
        message = "出现新的缺口，重新进入追踪"
        activity_type = ActivityType.REOPENED
    await repo.add_activity(
        SubscriptionActivity(
            subscription_id=subscription.id, type=activity_type, message=message
        )
    )


class SubscriptionService:
    """订阅的全生命周期编排。持久化走仓储，TMDB 建档走 MediaLibraryService。"""

    def __init__(self, session: AsyncSession, media_library: MediaLibraryService) -> None:
        self._session = session
        self._repo = SubscriptionRepository(session)
        self._media_repo = MediaItemRepository(session)
        self._library = media_library
        self._rule_sets = RuleSetService(session)

    # ------------------------------------------------------------------
    # prepare：订阅弹层的数据来源（建档 + 季集概览 + 已订检查）
    # ------------------------------------------------------------------

    async def prepare(
        self,
        kind: MediaKind,
        tmdb_id: int,
        *,
        douban_id: str | None = None,
        extra_aliases: tuple[str, ...] = (),
    ) -> tuple[MediaItem, list[MediaSeason], Subscription | None]:
        """建档/复用媒体条目，返回 (条目, 季列表, 已有订阅)。幂等，弹层打开即调用。"""
        item = await self._library.ensure_media_item(
            kind, tmdb_id, douban_id=douban_id, extra_aliases=extra_aliases
        )
        assert item.id is not None
        seasons = await self._media_repo.list_seasons(item.id)
        existing = await self._repo.get_by_media_item(item.id)
        return item, seasons, existing

    # ------------------------------------------------------------------
    # 创建：定义 E 并物化
    # ------------------------------------------------------------------

    async def create(
        self,
        kind: MediaKind,
        tmdb_id: int,
        *,
        selected_seasons: list[int] | None = None,
        follow_future: bool = False,
        rule_set_id: int | None = None,
        douban_id: str | None = None,
    ) -> Subscription:
        """创建订阅并生成初始工单。同一条目已有订阅时幂等返回已有（不改参数）。"""
        item, seasons, existing = await self.prepare(kind, tmdb_id, douban_id=douban_id)
        if existing is not None:
            logger.info("条目《%s》已有订阅 #%s，幂等返回", item.title, existing.id)
            return existing
        assert item.id is not None

        selected = self._validate_selection(kind, selected_seasons or [], seasons)
        if kind is MediaKind.MOVIE:
            follow_future = False  # 电影没有"生长"，开关无意义，落库前归一

        if rule_set_id is None:
            rule_set_id = (await self._rule_sets.ensure_default()).id
        else:
            await self._rule_sets.get(rule_set_id)  # 不存在则抛 404
        assert rule_set_id is not None

        subscription = await self._repo.save(
            Subscription(
                media_item_id=item.id,
                kind=kind.value,
                selected_seasons=selected,
                follow_future=follow_future,
                rule_set_id=rule_set_id,
                status=SubscriptionStatus.ACTIVE,
            )
        )
        assert subscription.id is not None

        units = expected_units(kind, seasons, selected, follow_future)
        rows = [self._to_wanted(subscription, unit) for unit in units]
        await self._repo.add_wanted(rows)
        await self._log(
            subscription,
            ActivityType.CREATED,
            self._created_message(item, kind, selected, follow_future, rows),
            payload={
                "selected_seasons": selected,
                "follow_future": follow_future,
                "wanted_total": len(rows),
            },
        )
        await self._recompute_status(subscription, item)
        logger.info(
            "已订阅《%s》(%s)：勾选季 %s，追新 %s，生成工单 %d 个",
            item.title,
            kind.value,
            selected or "无",
            "开" if follow_future else "关",
            len(units),
        )
        return subscription

    # ------------------------------------------------------------------
    # 修改：E 变更 → diff 重算
    # ------------------------------------------------------------------

    async def update(
        self,
        subscription_id: int,
        *,
        selected_seasons: list[int] | None = None,
        follow_future: bool | None = None,
        rule_set_id: int | None = None,
    ) -> Subscription:
        """修改 E 的定义（季选择/追新/规则组），diff 重算工单。

        diff 规则（不变量③）：
        - 新入域的单元 → 补工单（调度语义按当下重新判定）；
        - 出域且 status=wanted 的 → 删除；
        - **已 grabbed/downloaded 的一律保留**——现实不可逆，重新入域时也
          因此不会重复下载。
        """
        subscription = await self._get_or_404(subscription_id)
        item = await self._media_repo_get(subscription.media_item_id)

        if rule_set_id is not None and rule_set_id != subscription.rule_set_id:
            await self._rule_sets.get(rule_set_id)
            subscription.rule_set_id = rule_set_id

        kind = MediaKind(subscription.kind)
        seasons = await self._media_repo.list_seasons(subscription.media_item_id)
        if selected_seasons is not None:
            subscription.selected_seasons = self._validate_selection(
                kind, selected_seasons, seasons
            )
        if follow_future is not None:
            subscription.follow_future = follow_future if kind is MediaKind.TV else False

        expected = expected_units(
            kind, seasons, list(subscription.selected_seasons), subscription.follow_future
        )
        existing = await self._repo.list_wanted(subscription_id)
        existing_keys = {(w.season_number, w.episode_number) for w in existing}
        selected_set = set(subscription.selected_seasons)

        expected_keys = {(u.season_number, u.episode_number) for u in expected}
        to_add = [
            self._to_wanted(subscription, unit)
            for unit in expected
            if (unit.season_number, unit.episode_number) not in existing_keys
        ]
        # 出域判定不能只看当前 E 快照：经追新进入的单元播出之后就不在"评估时刻
        # 的未来集"里了，但只要追新开关还开着，它们仍在域内。没有来源字段时用
        # 播出日期区分血统：air_date 晚于订阅创建（或未定档）的单元视作追新进入，
        # 开关开着即受保护；早于创建的只可能来自勾选季，季被取消即出域。
        created_date = subscription.created_at.date()
        def _protected_by_follow(w: WantedItem) -> bool:
            return (
                subscription.follow_future
                and w.season_number != 0
                and (w.air_date is None or w.air_date > created_date)
            )

        to_remove = [
            w
            for w in existing
            if w.status == WantedStatus.WANTED
            and (w.season_number, w.episode_number) not in expected_keys
            and w.season_number not in selected_set
            and not _protected_by_follow(w)
        ]

        await self._repo.save(subscription)
        if to_add:
            await self._repo.add_wanted(to_add)
        if to_remove:
            await self._repo.delete_wanted(to_remove)
        season_text = self._season_text(list(subscription.selected_seasons))
        await self._log(
            subscription,
            ActivityType.ADJUSTED,
            f"调整订阅：勾选{season_text}，持续追新{'开' if subscription.follow_future else '关'}；"
            f"补 {len(to_add)} 个工单，移除 {len(to_remove)} 个未完成工单"
            "（已提交下载的保留，不会重复下载）",
            payload={
                "selected_seasons": list(subscription.selected_seasons),
                "follow_future": subscription.follow_future,
                "added": len(to_add),
                "removed": len(to_remove),
            },
        )
        await self._recompute_status(subscription, item)
        logger.info(
            "订阅 #%s 已调整：补工单 %d 个，移除未完成工单 %d 个",
            subscription_id,
            len(to_add),
            len(to_remove),
        )
        return subscription

    # ------------------------------------------------------------------
    # 状态操作与查询
    # ------------------------------------------------------------------

    async def set_paused(self, subscription_id: int, paused: bool) -> Subscription:
        """暂停/恢复。暂停是用户显式状态；恢复后由派生重算落到 active/completed。"""
        subscription = await self._get_or_404(subscription_id)
        if paused:
            subscription.status = SubscriptionStatus.PAUSED
            await self._repo.save(subscription)
            await self._log(
                subscription,
                ActivityType.PAUSED,
                "已暂停：资源匹配与搜索将跳过该订阅，随时可恢复",
            )
            return subscription
        subscription.status = SubscriptionStatus.ACTIVE
        await self._repo.save(subscription)
        await self._log(subscription, ActivityType.RESUMED, "已恢复追踪")
        item = await self._media_repo_get(subscription.media_item_id)
        await self._recompute_status(subscription, item)
        return subscription

    async def delete(self, subscription_id: int) -> None:
        """删除订阅（工单级联删除；不动已下载内容与下载器任务）。"""
        subscription = await self._get_or_404(subscription_id)
        await self._repo.delete(subscription)
        logger.info("订阅 #%s 已删除", subscription_id)

    async def list_with_progress(
        self, *, kind: str | None = None
    ) -> list[tuple[Subscription, MediaItem, dict[str, int]]]:
        """列表页数据：订阅 + 条目 + 工单状态分布（一次分组统计，不逐条查）。"""
        subscriptions = await self._repo.list_all(kind=kind)
        counts = await self._repo.count_wanted_by_status(
            [s.id for s in subscriptions if s.id is not None]
        )
        rows: list[tuple[Subscription, MediaItem, dict[str, int]]] = []
        for sub in subscriptions:
            item = await self._media_repo_get(sub.media_item_id)
            rows.append((sub, item, counts.get(sub.id or -1, {})))
        return rows

    async def detail(
        self, subscription_id: int
    ) -> tuple[Subscription, MediaItem, list[WantedItem]]:
        subscription = await self._get_or_404(subscription_id)
        item = await self._media_repo_get(subscription.media_item_id)
        wanted = await self._repo.list_wanted(subscription_id)
        return subscription, item, wanted

    # ------------------------------------------------------------------
    # 工单构造与派生状态（核心逻辑在模块级函数，服务只做委托）
    # ------------------------------------------------------------------

    def _to_wanted(self, subscription: Subscription, unit: ExpectedUnit) -> WantedItem:
        assert subscription.id is not None
        next_search, priority = schedule_for(subscription.kind, unit)
        return WantedItem(
            subscription_id=subscription.id,
            media_item_id=subscription.media_item_id,
            season_number=unit.season_number,
            episode_number=unit.episode_number,
            status=WantedStatus.WANTED,
            air_date=unit.air_date,
            priority=priority,
            next_search_at=next_search,
        )

    async def _recompute_status(self, subscription: Subscription, item: MediaItem) -> None:
        await recompute_subscription_status(self._session, subscription, item)

    async def activities(
        self, subscription_id: int, *, limit: int = 100
    ) -> list[SubscriptionActivity]:
        """订阅活动流水（时间倒序）——详情页时间线的数据源。"""
        await self._get_or_404(subscription_id)
        return await self._repo.list_activities(subscription_id, limit=limit)

    # ------------------------------------------------------------------
    # 活动流水（透明化：每个动作落一条中文可读记录）
    # ------------------------------------------------------------------

    async def _log(
        self,
        subscription: Subscription,
        activity_type: ActivityType,
        message: str,
        *,
        wanted_item_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        assert subscription.id is not None
        await self._repo.add_activity(
            SubscriptionActivity(
                subscription_id=subscription.id,
                wanted_item_id=wanted_item_id,
                type=activity_type,
                message=message,
                payload=payload or {},
            )
        )

    def _created_message(
        self,
        item: MediaItem,
        kind: MediaKind,
        selected: list[int],
        follow_future: bool,
        rows: list[WantedItem],
    ) -> str:
        """创建活动的中文摘要：把 E 的定义和调度分布一句话说清楚。"""
        if kind is MediaKind.MOVIE:
            return f"创建订阅《{item.title}》：已加入搜索队列，等待搜索任务寻找资源"
        now = utcnow()
        immediate = sum(
            1 for w in rows if w.next_search_at is not None and w.next_search_at <= now
        )
        future = sum(
            1 for w in rows if w.next_search_at is not None and w.next_search_at > now
        )
        undated = sum(1 for w in rows if w.next_search_at is None)
        parts = []
        if immediate:
            parts.append(f"{immediate} 集已播出、排入搜索队列")
        if future:
            parts.append(f"{future} 集未播出、播出后先等被动匹配")
        if undated:
            parts.append(f"{undated} 集未定档、定档后再安排")
        detail = "；".join(parts) if parts else "暂无待办集"
        return (
            f"创建订阅《{item.title}》：勾选{self._season_text(selected)}，"
            f"持续追新{'开' if follow_future else '关'}；"
            f"共生成 {len(rows)} 个追踪项——{detail}"
        )

    @staticmethod
    def _season_text(selected: list[int]) -> str:
        if not selected:
            return "（未勾选任何季）"
        return "第 " + "、".join(str(n) for n in selected) + " 季"

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_selection(
        kind: MediaKind, selected: list[int], seasons: list[MediaSeason]
    ) -> list[int]:
        if kind is MediaKind.MOVIE:
            if selected:
                raise BadRequestException("电影订阅不支持季选择")
            return []
        known = {s.season_number for s in seasons}
        unknown = sorted(set(selected) - known)
        if unknown:
            raise BadRequestException(f"该剧不存在这些季：{unknown}")
        return sorted(set(selected))

    @staticmethod
    def _is_movie_unit(wanted: WantedItem) -> bool:
        return wanted.season_number == 0 and wanted.episode_number == 0

    async def _get_or_404(self, subscription_id: int) -> Subscription:
        subscription = await self._repo.get(subscription_id)
        if subscription is None:
            raise NotFoundException(f"订阅不存在：#{subscription_id}")
        return subscription

    async def _media_repo_get(self, media_item_id: int) -> MediaItem:
        item = await self._session.get(MediaItem, media_item_id)
        if item is None:  # 外键保证下理论不可达
            raise NotFoundException("订阅关联的媒体条目不存在")
        return item


def _parse_date(iso_date: str | None) -> date | None:
    if not iso_date:
        return None
    try:
        return date.fromisoformat(iso_date)
    except ValueError:
        return None
