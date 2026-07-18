from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import BadRequestException, NotFoundException
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.schemas.search import (
    CategoryTabItem,
    MediaSearchSnapshotView,
    PresetTabItem,
    SearchHistoryItem,
    SearchPreferencesUpdate,
    SearchPreferencesView,
    SearchResponse,
    SearchSnapshotView,
    SearchStreamDone,
    SearchTabItem,
    SiteSearchStatus,
    SiteStreamResult,
    TorrentHit,
)
from movieclaw_api.services.site_catalog import SiteCatalogService
from movieclaw_api.services.site_search import (
    search_all_sites,
    stream_search_all_sites,
)
from movieclaw_api.settings.schemas import (
    MAX_SEARCH_PRESETS,
    SearchCategoryTab,
    SearchPresetTab,
    SearchTab,
    get_search_tabs,
    save_search_tabs,
)
from movieclaw_db.engine import get_database, get_session
from movieclaw_db.repositories.search_history_repo import SearchHistoryRepository
from movieclaw_tracker.models import TorrentCategory

logger = logging.getLogger("movieclaw_api.search")

router = APIRouter(prefix="/search", tags=["search"])


@router.get(
    "",
    response_model=ApiResponse[SearchResponse],
    summary="跨站点并发搜索种子资源（支持多分类与站点子集）",
)
async def search_torrents(
    keyword: str = Query(..., min_length=1, description="搜索关键词，支持 IMDb ID"),
    categories: list[TorrentCategory] | None = Query(
        None, description="分类组合过滤（可多值：categories=movie&categories=tv）；不传表示不限分类"
    ),
    sites: list[str] | None = Query(
        None, description="站点子集（可多值）；不传表示全部可用站点"
    ),
    label: str | None = Query(
        None,
        max_length=32,
        description="本次搜索的展示名（分类中文名/自定义分类名），仅用于历史与回显",
    ),
    no_history: bool = Query(
        False, description="无痕搜索：为 True 时本次搜索不写入搜索历史"
    ),
    poster_mode: bool = Query(
        False,
        description="发起搜索时的图览模式偏好，仅随历史留存，用于点历史重搜/看快照时还原展示模式",
    ),
    page: int = Query(1, ge=1, description="页码（各站点独立分页）"),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[SearchResponse]:
    """对「已启用且验证通过」的站点（可用 ``sites`` 圈定子集）并发发起搜索，合并结果后返回。

    单个站点失败（认证过期 / 网络异常等）不影响整体：其结果为空，并在
    ``data.sites[].error`` 里给出可读原因，供前端提示。
    """
    result = await search_all_sites(
        keyword=keyword, categories=categories, site_ids=sites, label=label, page=page
    )
    if not no_history:
        history_id = await _record_history(session, keyword, categories, sites, label, page, poster_mode)
        if history_id is not None:
            await _save_snapshot(history_id, result.items, result.sites, result.total)
    return ok(result)


async def _record_history(
    session: AsyncSession,
    keyword: str,
    categories: list[TorrentCategory] | None,
    sites: list[str] | None,
    label: str | None,
    page: int,
    poster_mode: bool = False,
) -> int | None:
    """只在第 1 页记录搜索历史：翻页是同一次搜索的延续，不该重复计数。

    历史记录是辅助功能，写入失败只记日志，绝不影响搜索结果的返回。

    :return: 历史行 id，供搜索完成后回写结果快照；未记录/失败返回 None。
    """
    if page != 1:
        return None
    try:
        return await SearchHistoryRepository(session).record(
            keyword,
            label=label,
            categories=[c.value for c in categories] if categories else None,
            site_ids=sites,
            poster_mode=poster_mode,
        )
    except Exception:  # noqa: BLE001 —— 历史写入失败不能拖垮搜索本身
        logger.warning("搜索历史写入失败（不影响本次搜索结果）", exc_info=True)
        return None


async def _save_snapshot(
    history_id: int,
    items: list[TorrentHit],
    statuses: list[SiteSearchStatus],
    total: int,
    elapsed_ms: int | None = None,
) -> None:
    """把本次搜索的完整结果集回写到历史行，作为可回看的快照。

    用独立会话而非请求级 session：流式端点在响应开始后才拿到完整结果，
    彼时请求级 session 的生命周期已不可靠。快照是辅助功能，失败只记日志。
    """
    payload = json.dumps(
        {
            "total": total,
            "elapsed_ms": elapsed_ms,
            "items": [item.model_dump(mode="json") for item in items],
            "sites": [status.model_dump(mode="json") for status in statuses],
        },
        ensure_ascii=False,
    )
    try:
        async with get_database().session() as session:
            await SearchHistoryRepository(session).save_snapshot(history_id, payload)
    except Exception:  # noqa: BLE001 —— 快照写入失败不能影响搜索结果的送达
        logger.warning("搜索结果快照写入失败（不影响本次搜索）", exc_info=True)


@router.get(
    "/stream",
    summary="跨站点流式搜索（SSE）：站点开始/返回结果/失败逐事件实时推送",
)
async def search_torrents_stream(
    keyword: str = Query(..., min_length=1, description="搜索关键词，支持 IMDb ID"),
    categories: list[TorrentCategory] | None = Query(
        None, description="分类组合过滤（可多值）；不传表示不限分类"
    ),
    sites: list[str] | None = Query(
        None, description="站点子集（可多值）；不传表示全部可用站点"
    ),
    label: str | None = Query(
        None,
        max_length=32,
        description="本次搜索的展示名（分类中文名/自定义分类名），仅用于历史与回显",
    ),
    no_history: bool = Query(
        False, description="无痕搜索：为 True 时本次搜索不写入搜索历史"
    ),
    poster_mode: bool = Query(
        False,
        description="发起搜索时的图览模式偏好，仅随历史留存，用于点历史重搜/看快照时还原展示模式",
    ),
    page: int = Query(1, ge=1, description="页码（各站点独立分页）"),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """``/search`` 的流式版本：以 Server-Sent Events 逐事件推送搜索过程与结果。

    事件序列 ``start → site_start × N → (site_result | site_error) × N → done``，
    快的站点先出结果，前端边收边渲染，不再被最慢的站点拖住。载荷结构见
    schemas.search 的流式事件段；错误隔离口径与阻塞版一致（单站失败仅产生
    ``site_error`` 事件，不中断整个流）。
    """
    # 历史在流开始前落库：流式响应返回后请求级 session 的生命周期不再可靠
    history_id: int | None = None
    if not no_history:
        history_id = await _record_history(session, keyword, categories, sites, label, page, poster_mode)

    async def event_source():
        # 边转发边收集完整结果集，流正常走完（收到 done）后回写快照。
        # 客户端中途断开则不快照——不完整的结果没有留存价值，保留上一份完整快照更有用。
        collected: list[TorrentHit] = []
        done: SearchStreamDone | None = None
        async for event, payload in stream_search_all_sites(
            keyword=keyword, categories=categories, site_ids=sites, label=label, page=page
        ):
            if isinstance(payload, SiteStreamResult):
                collected.extend(payload.items)
            elif isinstance(payload, SearchStreamDone):
                done = payload
            yield f"event: {event}\ndata: {payload.model_dump_json()}\n\n"
        if history_id is not None and done is not None:
            await _save_snapshot(
                history_id, collected, done.sites, done.total, done.elapsed_ms
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # SSE 反缓冲三件套，缺一都会让事件被攒到连接结束才一次性吐出：
            # no-transform 阻止中间层 gzip（压缩器会攒块，Next 代理/CDN 均受影响）；
            # X-Accel-Buffering 关掉 Nginx 反代的响应缓冲；keep-alive 保持长连接。
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _preferences_view(tabs: list[SearchTab]) -> SearchPreferencesView:
    """把存储层的标签列表转成 API 视图（str id → 枚举）。"""
    items: list[SearchTabItem] = []
    for tab in tabs:
        if isinstance(tab, SearchCategoryTab):
            items.append(
                CategoryTabItem(id=TorrentCategory(tab.id), visible=tab.visible)
            )
        else:
            items.append(
                PresetTabItem(
                    id=tab.id,
                    name=tab.name,
                    visible=tab.visible,
                    categories=[TorrentCategory(c) for c in tab.categories],
                    site_ids=tab.site_ids,
                    poster_mode=tab.poster_mode,
                    skip_history=tab.skip_history,
                )
            )
    return SearchPreferencesView(tabs=items)


def _validate_tabs(payload: SearchPreferencesUpdate) -> None:
    """保存前的业务校验（结构校验已由 Pydantic 完成），全部中文报错。"""
    category_ids = [t.id for t in payload.tabs if isinstance(t, CategoryTabItem)]
    if len(category_ids) != len(set(category_ids)):
        raise BadRequestException("分类列表存在重复项，请刷新页面后重试")

    presets = [t for t in payload.tabs if isinstance(t, PresetTabItem)]
    if len(presets) > MAX_SEARCH_PRESETS:
        raise BadRequestException(f"自定义分类最多创建 {MAX_SEARCH_PRESETS} 个")
    preset_ids = [p.id for p in presets]
    if len(preset_ids) != len(set(preset_ids)):
        raise BadRequestException("自定义分类的标识重复，请刷新页面后重试")
    names = [p.name for p in presets]
    if len(names) != len(set(names)):
        dup = next(n for n in names if names.count(n) > 1)
        raise BadRequestException(f"已存在同名的自定义分类：{dup}")

    # 站点存在性：只认站点目录里的 site_id（目录是静态受支持集合）。
    # 站点是否「已配置/可用」不在此校验——预设允许先建后配，搜索时自动跳过不可用站点。
    known_sites = {cfg.site_id for cfg in SiteCatalogService().list_catalog()}
    for preset in presets:
        unknown = [s for s in preset.site_ids if s not in known_sites]
        if unknown:
            raise BadRequestException(
                f"自定义分类「{preset.name}」勾选了不存在的站点：{'、'.join(unknown)}"
            )


@router.get(
    "/preferences",
    response_model=ApiResponse[SearchPreferencesView],
    summary="读取搜索偏好（标签栏：内置分类 + 自定义分类）",
)
async def get_preferences() -> ApiResponse[SearchPreferencesView]:
    """返回全量标签的有序列表（含隐藏项）。

    搜索面板据此渲染分类栏（只取 visible=True 的），设置页则完整渲染
    供用户调整。偏好存服务端，跨设备一次保存处处生效。
    """
    return ok(_preferences_view(await get_search_tabs()))


@router.put(
    "/preferences",
    response_model=ApiResponse[SearchPreferencesView],
    summary="保存搜索偏好（标签栏：内置分类 + 自定义分类）",
)
async def update_preferences(
    payload: SearchPreferencesUpdate,
) -> ApiResponse[SearchPreferencesView]:
    """整体覆盖式保存标签配置，返回保存后的完整列表。

    校验：未知分类/名称超长在请求解析阶段被拒（422）；重复项、超出预设数量
    上限、勾选了不存在的站点报 400；缺失的内置分类由后端按默认可见性自动
    补齐到末尾（前端版本落后时也不丢数据）。
    """
    _validate_tabs(payload)
    tabs: list[SearchTab] = []
    for item in payload.tabs:
        if isinstance(item, CategoryTabItem):
            tabs.append(SearchCategoryTab(id=item.id.value, visible=item.visible))
        else:
            tabs.append(
                SearchPresetTab(
                    id=item.id,
                    name=item.name,
                    visible=item.visible,
                    categories=[c.value for c in item.categories],
                    site_ids=item.site_ids,
                    poster_mode=item.poster_mode,
                    skip_history=item.skip_history,
                )
            )
    saved = await save_search_tabs(tabs)
    return ok(_preferences_view(saved), message="搜索设置已保存")


@router.get(
    "/history",
    response_model=ApiResponse[list[SearchHistoryItem]],
    summary="获取最近的搜索历史",
)
async def list_search_history(
    limit: int = Query(10, ge=1, le=50, description="返回关键词组数上限"),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[SearchHistoryItem]]:
    """返回最近的关键词组；组内保留媒体及各资源分类的独立记录与快照。"""
    rows = await SearchHistoryRepository(session).list_recent_groups(limit)
    return ok([SearchHistoryItem.from_model(r) for r in rows])


@router.get(
    "/history/{history_id}/snapshot",
    response_model=ApiResponse[SearchSnapshotView],
    summary="读取某条搜索历史的结果快照",
)
async def get_search_snapshot(
    history_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[SearchSnapshotView]:
    """返回该历史行最近一次搜索的完整结果快照（items/sites/total + 快照时间）。

    前端点历史记录先渲染快照（秒开、不打扰站点），页顶提示条告知快照年龄，
    需要新数据时再「重新搜索」。历史不存在或尚无快照均报 404，前端据此
    回退为直接发起实时搜索。
    """
    repo = SearchHistoryRepository(session)
    row = await repo.get_by_id(history_id)
    if row is None:
        raise NotFoundException("该搜索历史不存在或已被删除")
    if row.vertical != "torrent":
        raise NotFoundException("该历史是影视搜索，请改用 media-snapshot 接口读取快照")
    if not row.snapshot_json or row.snapshot_at is None:
        raise NotFoundException("该搜索历史还没有结果快照，重新搜索后会自动生成")
    data = json.loads(row.snapshot_json)
    assert row.id is not None  # 从库里读出的记录必有主键
    return ok(
        SearchSnapshotView(
            history_id=row.id,
            keyword=row.keyword,
            label=row.label,
            categories=repo.parse_snapshot(row.categories_json),
            site_ids=repo.parse_snapshot(row.site_ids_json),
            snapshot_at=row.snapshot_at,
            total=data["total"],
            # 老快照（加字段前生成的）没有 elapsed_ms 键，get 兜底为 None
            elapsed_ms=data.get("elapsed_ms"),
            items=data["items"],
            sites=data["sites"],
        )
    )


@router.get(
    "/history/{history_id}/media-snapshot",
    response_model=ApiResponse[MediaSearchSnapshotView],
    summary="读取某条媒体搜索历史的结果快照",
)
async def get_media_search_snapshot(
    history_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[MediaSearchSnapshotView]:
    """返回该媒体搜索历史（vertical=media）最近一次的豆瓣条目快照。

    与种子快照分成两个端点：载荷结构完全不同，前端从历史行的 ``vertical``
    即知该调哪个。历史不存在 / 不是媒体搜索 / 尚无快照均报 404，前端据此
    回退为直接发起实时搜索。
    """
    row = await SearchHistoryRepository(session).get_by_id(history_id)
    if row is None:
        raise NotFoundException("该搜索历史不存在或已被删除")
    if row.vertical != "media":
        raise NotFoundException("该历史是站点资源搜索，请改用 snapshot 接口读取快照")
    if not row.snapshot_json or row.snapshot_at is None:
        raise NotFoundException("该搜索历史还没有结果快照，重新搜索后会自动生成")
    data = json.loads(row.snapshot_json)
    assert row.id is not None  # 从库里读出的记录必有主键
    return ok(
        MediaSearchSnapshotView(
            history_id=row.id,
            keyword=row.keyword,
            snapshot_at=row.snapshot_at,
            total=data["total"],
            items=data["items"],
        )
    )


@router.delete(
    "/history/{history_id}",
    response_model=ApiResponse[None],
    summary="删除单条搜索历史",
)
async def delete_search_history(
    history_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[None]:
    deleted = await SearchHistoryRepository(session).delete_by_id(history_id)
    if not deleted:
        raise NotFoundException("该搜索历史不存在或已被删除")
    return ok(None, message="已删除")


@router.delete(
    "/history",
    response_model=ApiResponse[None],
    summary="清空搜索历史",
)
async def clear_search_history(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[None]:
    count = await SearchHistoryRepository(session).clear()
    return ok(None, message=f"已清空 {count} 条搜索历史")
