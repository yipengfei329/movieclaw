from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.media_item import MediaItem, MediaSeason


class MediaItemRepository:
    """媒体条目（``media_item`` / ``media_season``）的数据访问层。

    职责边界：只做按锚存取与整体落库，不理解 TMDB 数据结构、不做收敛判定——
    这些语义在 ``movieclaw_api.services.media_library`` 与 ``movieclaw_media.library``。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_anchor(self, kind: str, tmdb_id: int) -> MediaItem | None:
        """按唯一锚 (kind, tmdb_id) 读取条目；不存在返回 None。"""
        result = await self._session.execute(
            select(MediaItem).where(MediaItem.kind == kind, MediaItem.tmdb_id == tmdb_id)
        )
        return result.scalar_one_or_none()

    async def list_seasons(self, media_item_id: int) -> list[MediaSeason]:
        """返回条目的全部季，按季号升序（特别季 0 在最前）。"""
        result = await self._session.execute(
            select(MediaSeason)
            .where(MediaSeason.media_item_id == media_item_id)
            .order_by(MediaSeason.season_number)
        )
        return list(result.scalars().all())

    async def create_with_seasons(
        self, item: MediaItem, seasons: list[MediaSeason]
    ) -> MediaItem:
        """建档：条目与季一次事务落库，返回带 id 的条目。"""
        self._session.add(item)
        await self._session.flush()  # 先拿到 item.id 供季行引用
        for season in seasons:
            season.media_item_id = item.id
            self._session.add(season)
        await self._session.commit()
        await self._session.refresh(item)
        return item

    async def rollback(self) -> None:
        """回滚当前事务（并发建档撞唯一锚后，会话须先回滚才能继续查询）。"""
        await self._session.rollback()

    async def save(self, item: MediaItem) -> MediaItem:
        """保存对已加载条目的修改（如回填 douban_id / 合并别名）。"""
        item.updated_at = utcnow()
        self._session.add(item)
        await self._session.commit()
        await self._session.refresh(item)
        return item
