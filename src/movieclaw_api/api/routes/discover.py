"""发现页接口：发现电影 / 发现剧集的聚合数据与条目详情（数据源 TMDB）。

路由保持薄：编排逻辑全部在 movieclaw_media.service，这里只做两件事——
调服务、把 TMDB 领域错误翻译成 API 层的统一异常（中文提示直达前端）。
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import (
    AppException,
    NotFoundException,
    UpstreamServiceException,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.media_discover import get_douban_media_service, get_media_service
from movieclaw_db.engine import get_session
from movieclaw_db.repositories.search_history_repo import SearchHistoryRepository
from movieclaw_media import (
    DiscoverPage,
    DoubanError,
    MediaDetail,
    MediaKind,
    MediaSearchItem,
    TmdbError,
    TmdbNotFoundError,
)

logger = logging.getLogger("movieclaw_api.discover")

router = APIRouter(prefix="/discover", tags=["discover"])


class DiscoverSource(StrEnum):
    """发现页可切换的数据视角。"""

    TMDB = "tmdb"
    DOUBAN = "douban"


def _translate(exc: TmdbError | DoubanError) -> AppException:
    """上游影视数据错误 → API 统一异常（message 已是面向用户的中文）。"""
    if isinstance(exc, TmdbNotFoundError):
        return NotFoundException(str(exc))
    return UpstreamServiceException(str(exc))


@router.get(
    "/search",
    response_model=ApiResponse[list[MediaSearchItem]],
    summary="搜索影视元数据候选",
)
async def search_media(
    q: str = Query(min_length=1, max_length=100),
    source: DiscoverSource = Query(default=DiscoverSource.DOUBAN),
    history: bool = Query(
        False,
        description="是否记录搜索历史并留存结果快照（统一搜索入口传 True；"
        "发现页工具栏等场景默认不记录）",
    ),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[MediaSearchItem]]:
    """搜索指定元数据来源：豆瓣移动端轻量搜索 / TMDB multi 搜索。

    ``history=True`` 时把本次搜索记入搜索历史（vertical=media，与站点资源
    搜索混排展示）并留存结果快照——搜索失败不记录，空结果照常记录（「搜过
    但没找到」也是有效历史）。历史写入失败只记日志，不影响搜索结果返回。
    搜索页对同一关键词并行搜两个来源，历史只随豆瓣请求记一条，避免重复；
    因此 TMDB 来源忽略 history 参数。
    """
    try:
        if source is DiscoverSource.DOUBAN:
            results = await get_douban_media_service().search(q)
        else:
            results = await get_media_service().search(q)
    except (TmdbError, DoubanError) as exc:
        raise _translate(exc) from exc
    if history and source is DiscoverSource.DOUBAN:
        await _record_media_history(session, q, results)
    return ok(results)


@router.get(
    "/douban/{douban_id}",
    response_model=ApiResponse[MediaDetail],
    summary="豆瓣影视条目详情",
)
async def get_douban_media_detail(douban_id: str) -> ApiResponse[MediaDetail]:
    """返回豆瓣轻量详情；条目类型由豆瓣响应自动识别。"""
    try:
        detail = await get_douban_media_service().media_detail(douban_id)
    except DoubanError as exc:
        raise _translate(exc) from exc
    return ok(detail)


async def _record_media_history(
    session: AsyncSession, keyword: str, results: list[MediaSearchItem]
) -> None:
    """媒体搜索落历史 + 回写结果快照。辅助功能：任何失败只记日志。

    与种子搜索不同，本端点在请求内就拿到了完整结果，历史与快照可在同一个
    请求级 session 里一次写完，无需独立会话。
    """
    try:
        repo = SearchHistoryRepository(session)
        history_id = await repo.record(keyword, vertical="media")
        if history_id is None:
            return
        payload = json.dumps(
            {
                "total": len(results),
                "items": [item.model_dump(mode="json") for item in results],
            },
            ensure_ascii=False,
        )
        await repo.save_snapshot(history_id, payload)
    except Exception:  # noqa: BLE001 —— 历史写入失败不能拖垮搜索本身
        logger.warning("媒体搜索历史写入失败（不影响本次搜索结果）", exc_info=True)


@router.get(
    "/{kind}",
    response_model=ApiResponse[DiscoverPage],
    summary="发现页聚合数据（Hero 精选 + 分类横滚行）",
)
async def get_discover_page(
    kind: MediaKind,
    source: DiscoverSource = Query(default=DiscoverSource.TMDB),
) -> ApiResponse[DiscoverPage]:
    """返回一个完整发现页：kind=movie 发现电影，kind=tv 发现剧集。

    数据来自 TMDB 多个榜单的并发聚合，服务端缓存 30 分钟；单个榜单失败
    只会缺一行，全部失败才报错（如 Key 未配置/无效、网络不通）。
    """
    try:
        service = (
            get_douban_media_service() if source is DiscoverSource.DOUBAN else get_media_service()
        )
        page = await service.discover_page(kind)
    except (TmdbError, DoubanError) as exc:
        raise _translate(exc) from exc
    return ok(page)


@router.get(
    "/{kind}/{tmdb_id}",
    response_model=ApiResponse[MediaDetail],
    summary="影视条目详情（词条信息 + 相似推荐）",
)
async def get_media_detail(kind: MediaKind, tmdb_id: int) -> ApiResponse[MediaDetail]:
    """返回单个条目的详情：回填片长/季数的卡片字段、演职员等词条信息、相似推荐。"""
    try:
        detail = await get_media_service().media_detail(kind, tmdb_id)
    except TmdbError as exc:
        raise _translate(exc) from exc
    return ok(detail)
