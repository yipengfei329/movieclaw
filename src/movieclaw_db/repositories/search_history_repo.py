from __future__ import annotations

import json

from sqlalchemy import delete as sa_delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.search_history import SearchHistory


class SearchHistoryRepository:
    """搜索历史（``search_history`` 表）的数据访问层。

    职责：记录每次搜索（按「关键词 + 分类/站点组合快照」去重计数）、按最近
    搜索时间列出、逐条删除与清空。容量上限也在本层维护——``record`` 落库后
    顺手裁掉最旧的超额行，调用方无感。
    """

    # 历史记录保留上限。超出后按「最近一次搜索时间」淘汰最旧的行。
    # 前端一般只展示最近十来条，50 已留足余量；调大只影响这一处。
    MAX_ENTRIES = 50

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def snapshot(values: list[str] | None) -> str | None:
        """把组合列表归一化成可做相等比较的快照串：排序 + 去重 + JSON。

        空列表与 None 统一归一化为 None（语义都是「不限」），
        这样「不勾选任何分类」与「全部」在去重上是同一条历史。
        """
        if not values:
            return None
        return json.dumps(sorted(set(values)), ensure_ascii=False)

    @staticmethod
    def parse_snapshot(snapshot: str | None) -> list[str]:
        """快照串 → 列表；None（不限）返回空列表。"""
        return json.loads(snapshot) if snapshot else []

    async def record(
        self,
        keyword: str,
        label: str | None = None,
        categories: list[str] | None = None,
        site_ids: list[str] | None = None,
        poster_mode: bool = False,
        vertical: str = "torrent",
    ) -> int | None:
        """记录一次搜索：同 (keyword, 垂直, 组合快照) 已存在则累加次数，否则新建一行。

        ``updated_at`` 被刷新为当前时间，即「最近一次搜索时间」；``label`` 与
        ``poster_mode`` 一并刷新为最新值——它们是「怎么展示」而非「搜什么」，
        不参与去重键，同组合重搜只更新为最近一次的偏好。``vertical`` 参与去重：
        同一关键词分别搜媒体（豆瓣）和站点资源是两条独立历史，各自维护快照。

        :return: 本次搜索对应的历史行 id（关键词为空时返回 None），
            供搜索完成后回写结果快照（``save_snapshot``）。
        """
        keyword = keyword.strip()
        if not keyword:
            return None
        categories_json = self.snapshot(categories)
        site_ids_json = self.snapshot(site_ids)
        result = await self._session.execute(
            select(SearchHistory).where(
                SearchHistory.keyword == keyword,
                SearchHistory.vertical == vertical,
                # 快照为 None 时，SQLAlchemy 会把 == None 翻译成 IS NULL
                SearchHistory.categories_json == categories_json,
                SearchHistory.site_ids_json == site_ids_json,
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            row.search_count += 1
            row.label = label
            row.poster_mode = poster_mode
            row.updated_at = utcnow()
        else:
            row = SearchHistory(
                keyword=keyword,
                label=label,
                categories_json=categories_json,
                site_ids_json=site_ids_json,
                poster_mode=poster_mode,
                vertical=vertical,
            )
            self._session.add(row)
        await self._session.commit()
        await self._trim()
        return row.id

    async def get_by_id(self, history_id: int) -> SearchHistory | None:
        """按主键读取单条历史记录；不存在返回 None。"""
        return await self._session.get(SearchHistory, history_id)

    async def save_snapshot(self, history_id: int, snapshot_json: str) -> None:
        """把结果快照回写到历史行（覆盖旧快照），并刷新快照时间。

        历史行可能在搜索期间被用户删除或被容量裁剪淘汰——此时静默跳过，
        快照只是历史的附属物，行没了快照自然也不该存在。
        """
        row = await self._session.get(SearchHistory, history_id)
        if row is None:
            return
        row.snapshot_json = snapshot_json
        row.snapshot_at = utcnow()
        await self._session.commit()

    async def _trim(self) -> None:
        """把超出 MAX_ENTRIES 的最旧记录删掉，防止表无限膨胀。"""
        result = await self._session.execute(
            select(SearchHistory.id)
            .order_by(SearchHistory.updated_at.desc(), SearchHistory.id.desc())
            .offset(self.MAX_ENTRIES)
        )
        stale_ids = list(result.scalars().all())
        if not stale_ids:
            return
        await self._session.execute(sa_delete(SearchHistory).where(SearchHistory.id.in_(stale_ids)))
        await self._session.commit()

    async def list_recent_groups(self, limit: int = 10) -> list[SearchHistory]:
        """返回最近 ``limit`` 个关键词组及各组的全部范围记录。

        搜索结果页切换分类会产生同关键词、不同范围的独立历史和快照，这些记录
        必须保留；但接口若先按行 ``LIMIT``，一个高频关键词会挤掉其他关键词。
        因此先按去空格、忽略大小写后的关键词选出最近 N 组，再回表取组内全部行。
        返回顺序为组的最近时间倒序，组内仍按各范围的最近搜索时间倒序。
        """
        keyword_key = func.lower(func.trim(SearchHistory.keyword))
        recent_groups = (
            select(
                keyword_key.label("keyword_key"),
                func.max(SearchHistory.updated_at).label("group_updated_at"),
            )
            .group_by(keyword_key)
            .order_by(func.max(SearchHistory.updated_at).desc())
            .limit(limit)
            .subquery()
        )
        result = await self._session.execute(
            select(SearchHistory)
            .join(recent_groups, keyword_key == recent_groups.c.keyword_key)
            .order_by(
                recent_groups.c.group_updated_at.desc(),
                SearchHistory.updated_at.desc(),
                SearchHistory.id.desc(),
            )
        )
        return list(result.scalars().all())

    async def delete_by_id(self, history_id: int) -> bool:
        """删除单条历史记录，返回是否真的删了（不存在返回 False）。"""
        row = await self._session.get(SearchHistory, history_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True

    async def clear(self) -> int:
        """清空全部历史记录，返回删除条数。"""
        result = await self._session.execute(sa_delete(SearchHistory))
        await self._session.commit()
        return result.rowcount or 0
