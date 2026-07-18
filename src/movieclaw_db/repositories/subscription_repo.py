from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.rule_set import RuleSet
from movieclaw_db.models.subscription import Subscription, WantedItem
from movieclaw_db.models.subscription_activity import SubscriptionActivity


class RuleSetRepository:
    """规则组的数据访问层。禁删语义（被引用/默认组）由服务层判断，
    这里只提供"数它被多少订阅引用"的原始查询。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, rule_set_id: int) -> RuleSet | None:
        return await self._session.get(RuleSet, rule_set_id)

    async def get_default(self) -> RuleSet | None:
        result = await self._session.execute(
            select(RuleSet).where(RuleSet.is_default.is_(True))  # type: ignore[attr-defined]
        )
        return result.scalars().first()

    async def list_all(self) -> list[RuleSet]:
        result = await self._session.execute(select(RuleSet).order_by(RuleSet.id))
        return list(result.scalars().all())

    async def count_references(self, rule_set_id: int) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(Subscription)
            .where(Subscription.rule_set_id == rule_set_id)
        )
        return int(result.scalar_one())

    async def save(self, row: RuleSet) -> RuleSet:
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, row: RuleSet) -> None:
        await self._session.delete(row)
        await self._session.commit()


class SubscriptionRepository:
    """订阅与工单的数据访问层。

    工单的批量增删只在"订阅创建/修改 diff"里发生，都走本仓储；
    匹配管线（P4）对工单的状态推进走各自的条件更新，不经过这里。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    async def get(self, subscription_id: int) -> Subscription | None:
        return await self._session.get(Subscription, subscription_id)

    async def get_by_media_item(self, media_item_id: int) -> Subscription | None:
        result = await self._session.execute(
            select(Subscription).where(Subscription.media_item_id == media_item_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self, *, kind: str | None = None) -> list[Subscription]:
        query = select(Subscription).order_by(Subscription.created_at.desc())  # type: ignore[attr-defined]
        if kind is not None:
            query = query.where(Subscription.kind == kind)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def save(self, row: Subscription) -> Subscription:
        row.updated_at = utcnow()
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, row: Subscription) -> None:
        """删除订阅；工单随外键级联删除（不动已下载的文件与下载器任务）。"""
        await self._session.delete(row)
        await self._session.commit()

    # ------------------------------------------------------------------
    # 工单
    # ------------------------------------------------------------------

    async def list_wanted(self, subscription_id: int) -> list[WantedItem]:
        result = await self._session.execute(
            select(WantedItem)
            .where(WantedItem.subscription_id == subscription_id)
            .order_by(WantedItem.season_number, WantedItem.episode_number)
        )
        return list(result.scalars().all())

    async def add_wanted(self, rows: list[WantedItem]) -> None:
        """批量补工单；与订阅行的变更共用调用方的提交时机。"""
        for row in rows:
            self._session.add(row)
        await self._session.commit()

    async def delete_wanted(self, rows: list[WantedItem]) -> None:
        for row in rows:
            await self._session.delete(row)
        await self._session.commit()

    # ------------------------------------------------------------------
    # 活动流水（订阅可解释性的落点）
    # ------------------------------------------------------------------

    async def add_activity(self, row: SubscriptionActivity) -> None:
        """落一条活动。活动是历史事实，只增不改。"""
        self._session.add(row)
        await self._session.commit()

    async def list_activities(
        self, subscription_id: int, *, limit: int = 100
    ) -> list[SubscriptionActivity]:
        """按时间倒序返回活动流水（时间线展示：最新在前）。"""
        result = await self._session.execute(
            select(SubscriptionActivity)
            .where(SubscriptionActivity.subscription_id == subscription_id)
            .order_by(SubscriptionActivity.id.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(result.scalars().all())

    async def count_wanted_by_status(
        self, subscription_ids: list[int]
    ) -> dict[int, dict[str, int]]:
        """批量统计各订阅的工单状态分布：{订阅id: {状态: 数量}}——列表页进度用。"""
        if not subscription_ids:
            return {}
        result = await self._session.execute(
            select(WantedItem.subscription_id, WantedItem.status, func.count())
            .where(WantedItem.subscription_id.in_(subscription_ids))  # type: ignore[attr-defined]
            .group_by(WantedItem.subscription_id, WantedItem.status)
        )
        counts: dict[int, dict[str, int]] = {}
        for sub_id, status, count in result.all():
            counts.setdefault(sub_id, {})[status] = int(count)
        return counts
