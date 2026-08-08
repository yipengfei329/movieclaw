"""发现页接口：发现电影 / 发现剧集的布局、逐行数据与条目详情（TMDB / 豆瓣）。

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
    UpstreamUnreachableException,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.discover_library import DiscoverLibraryProjectionService
from movieclaw_api.services.media_discover import get_douban_media_service, get_media_service
from movieclaw_db.engine import get_session
from movieclaw_db.repositories.search_history_repo import SearchHistoryRepository
from movieclaw_media import (
    DiscoverLayout,
    DoubanDiscoverService,
    DoubanError,
    DoubanNetworkError,
    DoubanNotFoundError,
    MediaCard,
    MediaDetail,
    MediaDiscoverService,
    MediaKind,
    MediaRow,
    MediaSearchItem,
    TmdbError,
    TmdbNetworkError,
    TmdbNotFoundError,
)

logger = logging.getLogger("movieclaw_api.discover")

router = APIRouter(prefix="/discover", tags=["discover"])


class DiscoverSource(StrEnum):
    """发现页可切换的数据视角。"""

    TMDB = "tmdb"
    DOUBAN = "douban"


def _translate(exc: TmdbError | DoubanError) -> AppException:
    """上游影视数据错误 → API 统一异常（message 已是面向用户的中文）。

    网络级不可达（含熔断快速失败）给结构化的 UPSTREAM_UNREACHABLE：
    前端据此渲染「原因说明 + 跳转网络设置」的引导错误态。
    """
    if isinstance(exc, (TmdbNotFoundError, DoubanNotFoundError)):
        return NotFoundException(str(exc))
    if isinstance(exc, TmdbNetworkError):
        return UpstreamUnreachableException(
            str(exc),
            service="tmdb",
            hint="到「设置 → 网络」为 TMDB 配置代理或镜像地址，保存后用连通性测试验证",
        )
    if isinstance(exc, DoubanNetworkError):
        return UpstreamUnreachableException(
            str(exc),
            service="douban",
            hint="到「设置 → 网络」为豆瓣配置代理或反代地址，保存后用连通性测试验证",
        )
    return UpstreamServiceException(str(exc))


@router.get(
    "/search",
    response_model=ApiResponse[list[MediaSearchItem]],
    summary="搜索影视元数据候选",
    operation_id="discover.search",
)
async def search_media(
    q: str = Query(min_length=1, max_length=100),
    source: DiscoverSource = Query(
        default=DiscoverSource.DOUBAN, description="元数据来源：tmdb / douban"
    ),
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
    "/douban/collection/{collection_id}",
    response_model=ApiResponse[MediaRow],
    summary="豆瓣完整榜单（「看全部」落地页）",
    operation_id="discover.douban.collection",
)
async def get_douban_full_collection(
    collection_id: str,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[MediaRow]:
    """分页聚合返回一份完整榜单（如 Top 250、豆瓣高分电影 500 条）。

    仅开放服务端白名单内的榜单 ID，其余返回 404；冷缓存时聚合受豆瓣限速
    影响可能需要数秒，之后命中缓存即时返回。
    """
    try:
        row = await get_douban_media_service().full_collection(collection_id)
    except DoubanError as exc:
        raise _translate(exc) from exc
    await DiscoverLibraryProjectionService(session).apply_cards(row.items)
    return ok(row)


@router.get(
    "/douban/{douban_id}",
    response_model=ApiResponse[MediaDetail],
    summary="豆瓣影视条目详情",
    operation_id="discover.douban.show",
)
async def get_douban_media_detail(
    douban_id: str,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[MediaDetail]:
    """返回豆瓣轻量详情；条目类型由豆瓣响应自动识别。"""
    try:
        detail = await get_douban_media_service().media_detail(douban_id)
    except DoubanError as exc:
        raise _translate(exc) from exc
    await DiscoverLibraryProjectionService(session).apply_detail(detail)
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


def _discover_service(source: DiscoverSource) -> DoubanDiscoverService | MediaDiscoverService:
    """按数据源取发现页服务；TMDB 未配置 Key 时抛 TmdbNotConfiguredError。"""
    return get_douban_media_service() if source is DiscoverSource.DOUBAN else get_media_service()


# 发现页按「布局 + Hero + 单行」三个端点渐进提供（替代旧的整页聚合端点）：
# 布局是纯配置毫秒级返回，前端据此撑起骨架后逐行拉数据，先就绪的行先渲染。
# 豆瓣榜单受限速（每秒 1 个请求）约束，冷缓存整页聚合要十几秒，这是拆分的动因。
# 注意路由顺序：/{kind}/layout 与 /{kind}/hero 必须注册在 /{kind}/{tmdb_id} 之前，
# 否则会被后者按路径参数吞掉。


@router.get(
    "/{kind}/layout",
    response_model=ApiResponse[DiscoverLayout],
    summary="发现页布局（行清单，纯配置）",
    operation_id="discover.layout",
)
async def get_discover_layout(
    kind: MediaKind,
    source: DiscoverSource = Query(
        default=DiscoverSource.TMDB, description="数据来源：tmdb（热门榜单）/ douban（豆瓣榜单）"
    ),
) -> ApiResponse[DiscoverLayout]:
    """返回发现页的行清单与 Hero 有无；不触发任何上游请求。"""
    try:
        return ok(_discover_service(source).layout(kind))
    except (TmdbError, DoubanError) as exc:
        raise _translate(exc) from exc


@router.get(
    "/{kind}/hero",
    response_model=ApiResponse[list[MediaCard]],
    summary="发现页 Hero 大横幅精选",
    operation_id="discover.hero",
)
async def get_discover_hero(
    kind: MediaKind,
    source: DiscoverSource = Query(
        default=DiscoverSource.TMDB, description="数据来源：tmdb（热门榜单）/ douban（豆瓣榜单）"
    ),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[MediaCard]]:
    """返回 Hero 轮播精选；布局声明无 Hero 的数据源（豆瓣）返回空列表。"""
    try:
        hero = await _discover_service(source).discover_hero(kind)
    except (TmdbError, DoubanError) as exc:
        raise _translate(exc) from exc
    await DiscoverLibraryProjectionService(session).apply_cards(hero)
    return ok(hero)


@router.get(
    "/{kind}/rows/{row_id}",
    response_model=ApiResponse[MediaRow],
    summary="发现页单行数据",
    operation_id="discover.row",
)
async def get_discover_row(
    kind: MediaKind,
    row_id: str,
    source: DiscoverSource = Query(
        default=DiscoverSource.TMDB, description="数据来源：tmdb（热门榜单）/ douban（豆瓣榜单）"
    ),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[MediaRow]:
    """返回布局中一行的条目数据；条目太少的行返回空 items，由前端收起。"""
    try:
        row = await _discover_service(source).discover_row(kind, row_id)
    except (TmdbError, DoubanError) as exc:
        raise _translate(exc) from exc
    if row is None:
        raise NotFoundException(f"发现页布局中没有 id 为 {row_id} 的行")
    await DiscoverLibraryProjectionService(session).apply_cards(row.items)
    return ok(row)


@router.get(
    "/{kind}/{tmdb_id}",
    response_model=ApiResponse[MediaDetail],
    summary="影视条目详情（词条信息 + 相似推荐）",
    operation_id="discover.show",
)
async def get_media_detail(
    kind: MediaKind,
    tmdb_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[MediaDetail]:
    """返回单个条目的详情：回填片长/季数的卡片字段、演职员等词条信息、相似推荐。"""
    try:
        detail = await get_media_service().media_detail(kind, tmdb_id)
    except TmdbError as exc:
        raise _translate(exc) from exc
    await DiscoverLibraryProjectionService(session).apply_detail(detail)
    return ok(detail)
