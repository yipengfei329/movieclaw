from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.schemas.site import (
    CatalogItem,
    ConfiguredSite,
    SiteConfigCreate,
    SiteConfigUpdate,
    SiteStatusUpdate,
    SiteSyncStatsView,
)
from movieclaw_api.services import SiteCatalogService, SiteConfigService, verify_site
from movieclaw_db.engine import get_session
from movieclaw_db.models.site_credential import SiteCredential
from movieclaw_db.repositories.torrent_repo import TorrentRepository

router = APIRouter(prefix="/sites", tags=["sites"])


async def _to_view(service: SiteConfigService, row: SiteCredential) -> ConfiguredSite:
    """把 ORM 记录组装成对外视图，并附上该站点的用户资料快照。

    所有返回 ConfiguredSite 的端点都必须走这里带上 profile —— 前端在启停、
    重验等操作后会用响应整体替换本地状态，若响应缺 profile 会把已展示的
    资料"冲掉"。
    """
    return ConfiguredSite.from_model(row, await service.profile_of(row.site_id))


# ---------------------------------------------------------------------------
# 目录（可选项）
# ---------------------------------------------------------------------------


@router.get(
    "/catalog",
    response_model=ApiResponse[list[CatalogItem]],
    summary="列出系统支持的可配置站点及授权要求",
)
async def list_catalog() -> ApiResponse[list[CatalogItem]]:
    """返回所有"可选项"。前端据此展示可配置的站点，以及每个站点支持的
    授权类型和各自要填的字段。"""
    catalog = SiteCatalogService()
    items = [CatalogItem.from_config(c) for c in catalog.list_catalog()]
    return ok(items)


# ---------------------------------------------------------------------------
# 已配置站点（可用站点）
# ---------------------------------------------------------------------------


@router.get(
    "",
    response_model=ApiResponse[list[ConfiguredSite]],
    summary="列出用户已配置的站点及验证状态",
)
async def list_configured(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[ConfiguredSite]]:
    service = SiteConfigService(session)
    rows = await service.list_configured()
    # 一次批量查出全部站点的资料快照再拼装，避免逐站查询（N+1）
    profiles = await service.profiles_of([r.site_id for r in rows])
    return ok([ConfiguredSite.from_model(r, profiles.get(r.site_id)) for r in rows])


@router.get(
    "/sync-stats",
    response_model=ApiResponse[dict[str, SiteSyncStatsView]],
    summary="各站点的种子缓存量与同步节奏",
)
async def list_sync_stats(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict[str, SiteSyncStatsView]]:
    """按 site_id 返回定时同步任务维护的本地缓存统计：已缓存种子数、
    上次/下次同步时间、当前轮询间隔等，供站点配置页展示。

    从未同步过且无游标的站点不会出现在结果里，前端按「尚未同步」处理缺失键。
    注意：本路由必须注册在 ``/{site_id}`` 之前，否则会被当成站点 ID 匹配。
    """
    repo = TorrentRepository(session)
    counts = await repo.count_by_site()
    cursors = {c.site_id: c for c in await repo.all_cursors()}
    stats = {
        site_id: SiteSyncStatsView.from_parts(counts.get(site_id, 0), cursors.get(site_id))
        for site_id in counts.keys() | cursors.keys()
    }
    return ok(stats)


@router.get(
    "/{site_id}",
    response_model=ApiResponse[ConfiguredSite],
    summary="获取单个已配置站点详情",
)
async def get_configured(
    site_id: str,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[ConfiguredSite]:
    service = SiteConfigService(session)
    row = await service.get_configured(site_id)
    return ok(await _to_view(service, row))


@router.post(
    "",
    response_model=ApiResponse[ConfiguredSite],
    summary="配置一个站点（保存后异步验证）",
)
async def configure_site(
    payload: SiteConfigCreate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[ConfiguredSite]:
    """保存站点授权信息（状态置为 pending），并在后台异步发起有效性验证。

    接口立即返回，前端可轮询 GET /sites/{site_id} 观察 status 变化：
    pending → verifying → active（可用）/ failed（见 last_error）。
    """
    service = SiteConfigService(session)
    await service.configure(
        site_id=payload.site_id,
        auth_type=payload.auth_type,
        cookie=payload.cookie,
        api_key=payload.api_key,
        username=payload.username,
        password=payload.password,
        enabled=payload.enabled,
    )
    # 同步占位为 VERIFYING（关闭并发窗口），再排队后台任务执行真实验证
    row = await service.start_verification(payload.site_id)
    background_tasks.add_task(verify_site, payload.site_id)
    return ok(await _to_view(service, row), message="已保存，正在验证")


@router.put(
    "/{site_id}",
    response_model=ApiResponse[ConfiguredSite],
    summary="更新站点授权信息（更新后重新异步验证）",
)
async def update_site(
    site_id: str,
    payload: SiteConfigUpdate,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[ConfiguredSite]:
    service = SiteConfigService(session)
    await service.configure(
        site_id=site_id,
        auth_type=payload.auth_type,
        cookie=payload.cookie,
        api_key=payload.api_key,
        username=payload.username,
        password=payload.password,
        enabled=payload.enabled,
    )
    row = await service.start_verification(site_id)
    background_tasks.add_task(verify_site, site_id)
    return ok(await _to_view(service, row), message="已更新，正在验证")


@router.patch(
    "/{site_id}/status",
    response_model=ApiResponse[ConfiguredSite],
    summary="启用 / 停用站点",
)
async def set_site_status(
    site_id: str,
    payload: SiteStatusUpdate,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[ConfiguredSite]:
    service = SiteConfigService(session)
    row = await service.set_enabled(site_id, payload.enabled)
    return ok(await _to_view(service, row))


@router.post(
    "/{site_id}/verify",
    response_model=ApiResponse[ConfiguredSite],
    summary="手动重新触发验证",
)
async def reverify_site(
    site_id: str,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[ConfiguredSite]:
    """手动触发一次验证（如站点临时故障后想重试）。

    行为：站点未配置 → 404；已在验证中 → 409（避免重复触发）；否则同步占位为
    VERIFYING 并在后台重新走验证流程。"""
    service = SiteConfigService(session)
    row = await service.start_verification(site_id)  # 404/409/占位一步到位
    background_tasks.add_task(verify_site, site_id)
    return ok(await _to_view(service, row), message="已重新发起验证")


@router.delete(
    "/{site_id}",
    response_model=ApiResponse[dict],
    summary="删除站点配置（连带清理 cookie 缓存）",
)
async def delete_site(
    site_id: str,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = SiteConfigService(session)
    await service.delete(site_id)
    return ok({"site_id": site_id}, message="已删除")
