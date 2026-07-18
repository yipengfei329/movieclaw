from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.site_user_profile import SiteUserProfile


class ProfileRepository:
    """站点用户资料快照表的数据访问层。

    每个站点仅一行最新快照（覆盖式更新），由站点验证流程在验证成功后写入，
    站点设置页读取展示。详见 ``SiteUserProfile`` 模型说明。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_site(self, site_id: str) -> SiteUserProfile | None:
        """按站点标识查询资料快照；从未验证成功过则返回 None。"""
        result = await self._session.execute(
            select(SiteUserProfile).where(SiteUserProfile.site_id == site_id)
        )
        return result.scalar_one_or_none()

    async def get_many(self, site_ids: Iterable[str]) -> dict[str, SiteUserProfile]:
        """批量查询多个站点的资料快照，返回 site_id → 快照 的映射。

        供站点列表页一次性拼装使用，避免逐站查询（N+1）。
        """
        ids = list(site_ids)
        if not ids:
            return {}
        result = await self._session.execute(
            select(SiteUserProfile).where(SiteUserProfile.site_id.in_(ids))  # type: ignore[attr-defined]
        )
        return {row.site_id: row for row in result.scalars().all()}

    async def upsert(
        self,
        *,
        site_id: str,
        user_id: str,
        username: str,
        user_class: str = "",
        uploaded_bytes: int = 0,
        downloaded_bytes: int = 0,
        ratio: float | None = None,
        bonus: float | None = None,
        seeding_count: int = 0,
        leeching_count: int = 0,
        avatar_url: str | None = None,
        join_date: datetime | None = None,
    ) -> SiteUserProfile:
        """写入某站点的最新资料快照：存在则整行覆盖，不存在则新建。

        快照语义 —— 不做增量合并，每次验证成功都以站点返回的最新数据为准，
        并刷新 ``fetched_at`` 标记数据新鲜度。
        """
        # 全库约定存 naive UTC（见 models.base.utcnow）；站点解析出的注册日期
        # 可能带时区，这里统一归一，避免"写 aware / 读 naive"的比较错误
        if join_date is not None and join_date.tzinfo is not None:
            join_date = join_date.astimezone(UTC).replace(tzinfo=None)
        # 部分站点会给出无穷大分享率（如 TTG 的 VIP 用户）；inf/nan 无法被 JSON
        # 序列化（接口返回会直接报错），统一归一为 None（未知/不适用）
        if ratio is not None and not math.isfinite(ratio):
            ratio = None
        if bonus is not None and not math.isfinite(bonus):
            bonus = None
        now = utcnow()
        row = await self.get_by_site(site_id)
        if row is None:
            row = SiteUserProfile(
                site_id=site_id,
                user_id=user_id,
                username=username,
                user_class=user_class,
                uploaded_bytes=uploaded_bytes,
                downloaded_bytes=downloaded_bytes,
                ratio=ratio,
                bonus=bonus,
                seeding_count=seeding_count,
                leeching_count=leeching_count,
                avatar_url=avatar_url,
                join_date=join_date,
                fetched_at=now,
            )
            self._session.add(row)
        else:
            row.user_id = user_id
            row.username = username
            row.user_class = user_class
            row.uploaded_bytes = uploaded_bytes
            row.downloaded_bytes = downloaded_bytes
            row.ratio = ratio
            row.bonus = bonus
            row.seeding_count = seeding_count
            row.leeching_count = leeching_count
            row.avatar_url = avatar_url
            row.join_date = join_date
            row.fetched_at = now
            row.updated_at = now
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete(self, site_id: str) -> bool:
        """删除某站点的资料快照（随站点配置删除连带清理）。返回是否命中记录。"""
        row = await self.get_by_site(site_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
