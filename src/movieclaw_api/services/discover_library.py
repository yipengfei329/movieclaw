"""发现条目到本地媒体库存的轻量投影。

发现服务缓存的是上游 TMDB/豆瓣卡片，库存则随扫描实时变化。此模块在
API 边界按外部身份锚批量补齐库存摘要，避免领域服务依赖数据库，也不把
逐文件规格带进列表响应。
"""

from __future__ import annotations

from sqlalchemy import func, or_, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models import Library, LibraryFile, MediaItem
from movieclaw_media.models import (
    MediaCard,
    MediaDetail,
    MediaLibraryLink,
    MediaLibraryStatus,
    MediaSource,
)

_CardAnchor = tuple[str, str, str]


class DiscoverLibraryProjectionService:
    """把本地在位库存投影到发现卡片与详情入口。

    匹配只信任卡片来源对应的外部 ID：TMDB 使用 ``(kind, tmdb_id)``，
    豆瓣使用 ``(kind, douban_id)``。标题、年份等展示字段不参与查询，
    防止同名作品被误标为已入库。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def apply_cards(self, cards: list[MediaCard]) -> None:
        """批量回填卡片库存摘要；无匹配卡片明确重置为 ``None``。

        一次聚合查询同时统计每个条目的在位文件数和覆盖媒体库数；查询
        只取标量列，既不会读取文件路径，也不会反序列化音轨或字幕 JSON。
        """
        if not cards:
            return

        tmdb_anchors: set[tuple[str, int]] = set()
        douban_anchors: set[tuple[str, str]] = set()
        for card in cards:
            anchor = _anchor_for_card(card)
            if anchor is None:
                continue
            source, kind, external_id = anchor
            if source == MediaSource.TMDB:
                tmdb_anchors.add((kind, int(external_id)))
            else:
                douban_anchors.add((kind, external_id))

        statuses = await self._statuses(tmdb_anchors, douban_anchors)
        for card in cards:
            card.library_status = statuses.get(_anchor_for_card(card))

    async def apply_detail(self, detail: MediaDetail) -> None:
        """回填详情主条目与相关推荐摘要，并为主条目生成媒体库深链。"""
        await self.apply_cards([detail.card, *detail.related])
        status = detail.card.library_status
        detail.library_links = [] if status is None else await self._links_for(status.media_item_id)

    async def _statuses(
        self,
        tmdb_anchors: set[tuple[str, int]],
        douban_anchors: set[tuple[str, str]],
    ) -> dict[_CardAnchor, MediaLibraryStatus]:
        if not tmdb_anchors and not douban_anchors:
            return {}

        identity_filters = []
        if tmdb_anchors:
            identity_filters.append(tuple_(MediaItem.kind, MediaItem.tmdb_id).in_(tmdb_anchors))
        if douban_anchors:
            identity_filters.append(tuple_(MediaItem.kind, MediaItem.douban_id).in_(douban_anchors))

        # 从 LibraryFile 起表让 missing_since 过滤与计数都走库存事实；整条
        # 查询只选身份、库 ID 和聚合计数，列表请求不会碰文件明细字段。
        rows = (
            await self._session.execute(
                select(
                    MediaItem.id,
                    MediaItem.kind,
                    MediaItem.tmdb_id,
                    MediaItem.douban_id,
                    func.count(func.distinct(LibraryFile.library_id)),
                    func.count(LibraryFile.id),
                )
                .select_from(LibraryFile)
                .join(  # type: ignore[arg-type]
                    MediaItem, MediaItem.id == LibraryFile.media_item_id
                )
                .where(
                    LibraryFile.media_item_id.is_not(None),  # type: ignore[union-attr]
                    LibraryFile.missing_since.is_(None),  # type: ignore[union-attr]
                    or_(*identity_filters),
                )
                .group_by(
                    MediaItem.id,
                    MediaItem.kind,
                    MediaItem.tmdb_id,
                    MediaItem.douban_id,
                )
            )
        ).all()

        statuses: dict[_CardAnchor, MediaLibraryStatus] = {}
        ambiguous: set[_CardAnchor] = set()
        for media_item_id, kind, tmdb_id, douban_id, library_count, file_count in rows:
            assert media_item_id is not None
            status = MediaLibraryStatus(
                media_item_id=media_item_id,
                library_count=library_count,
                file_count=file_count,
            )
            _add_status(statuses, ambiguous, (MediaSource.TMDB, kind, str(tmdb_id)), status)
            if douban_id is not None:
                _add_status(statuses, ambiguous, (MediaSource.DOUBAN, kind, douban_id), status)
        return statuses

    async def _links_for(self, media_item_id: int) -> list[MediaLibraryLink]:
        """按用户媒体库排序返回一个条目在各库中的去重入口。"""
        rows = (
            await self._session.execute(
                select(Library.id, Library.name)
                .select_from(LibraryFile)
                .join(Library, Library.id == LibraryFile.library_id)  # type: ignore[arg-type]
                .where(
                    LibraryFile.media_item_id == media_item_id,
                    LibraryFile.media_item_id.is_not(None),  # type: ignore[union-attr]
                    LibraryFile.missing_since.is_(None),  # type: ignore[union-attr]
                )
                .group_by(Library.id, Library.name, Library.sort_order)
                .order_by(Library.sort_order, Library.id)
            )
        ).all()
        return [
            MediaLibraryLink(
                library_id=library_id,
                library_name=library_name,
                media_item_id=media_item_id,
            )
            for library_id, library_name in rows
        ]


def _anchor_for_card(card: MediaCard) -> _CardAnchor | None:
    """从来源卡片取得精确库存匹配键；非法 TMDB ID 直接视为未匹配。"""
    kind = card.type.value
    if card.source is MediaSource.TMDB:
        try:
            return (MediaSource.TMDB, kind, str(int(card.id)))
        except (TypeError, ValueError):
            return None
    if card.source is MediaSource.DOUBAN and card.id:
        return (MediaSource.DOUBAN, kind, card.id)
    return None


def _add_status(
    statuses: dict[_CardAnchor, MediaLibraryStatus],
    ambiguous: set[_CardAnchor],
    anchor: _CardAnchor,
    status: MediaLibraryStatus,
) -> None:
    """去除异常重复的豆瓣身份锚，绝不随机挑一个本地条目深链。"""
    if anchor in ambiguous:
        return
    existing = statuses.get(anchor)
    if existing is None:
        statuses[anchor] = status
    elif existing.media_item_id != status.media_item_id:
        statuses.pop(anchor)
        ambiguous.add(anchor)
