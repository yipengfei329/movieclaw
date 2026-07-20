from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.schemas.downloader import (
    DownloaderPayload,
    DownloaderStatusUpdate,
    DownloaderView,
    DownloadSubmitPayload,
    DownloadSubmitView,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.downloader_config import (
    DownloaderConfigService,
    verify_downloader,
)
from movieclaw_api.services.library_config import LibraryConfigService, derive_save_path
from movieclaw_api.services.torrent_submit import submit_torrent
from movieclaw_db.engine import get_session

router = APIRouter(prefix="/downloaders", tags=["downloaders"])


@router.post(
    "/submit",
    response_model=ApiResponse[DownloadSubmitView],
    summary="把一条搜索结果种子提交到默认下载器",
)
async def submit_download(
    payload: DownloadSubmitPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloadSubmitView]:
    """手动下载：带站点登录态取回 .torrent → 提交给默认下载器。

    保存目录三级取值：选了媒体库 → 库推导路径（主根/标题 (年份)，无标题时
    落库主根）；未选库 → 默认下载器配置的默认目录 → 下载器自身默认。
    提交幂等：种子已在下载器中不视为错误，data.already_exists=true。
    """
    library = None
    derived_path = None
    if payload.library_id is not None:
        library = await LibraryConfigService(session).get(payload.library_id)
        if payload.title:
            derived_path = derive_save_path(library, title=payload.title, year=payload.year)
        else:
            derived_path = library.primary_root

    result, row = await submit_torrent(
        session,
        site_id=payload.site_id,
        download_url=payload.download_url,
        tags=["movieclaw-manual"],
        save_path=derived_path,
    )
    assert row.id is not None  # 落库记录必有主键
    view = DownloadSubmitView(
        info_hash=result.info_hash,
        name=result.name,
        already_exists=result.already_exists,
        downloader_id=row.id,
        downloader_name=row.name,
        save_path=derived_path or row.save_path,
    )
    if result.already_exists:
        message = "该种子已在下载器中，未重复添加"
    elif library is not None:
        message = f"已提交到「{row.name}」，入库到「{library.name}」"
    else:
        message = f"已提交到「{row.name}」"
    return ok(view, message=message)


@router.get(
    "",
    response_model=ApiResponse[list[DownloaderView]],
    summary="列出已配置的下载器及连接状态",
)
async def list_downloaders(
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[DownloaderView]]:
    service = DownloaderConfigService(session)
    rows = await service.list_all()
    return ok([DownloaderView.from_model(r) for r in rows])


@router.get(
    "/{downloader_id}",
    response_model=ApiResponse[DownloaderView],
    summary="获取单个下载器详情",
)
async def get_downloader(
    downloader_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloaderView]:
    service = DownloaderConfigService(session)
    return ok(DownloaderView.from_model(await service.get(downloader_id)))


@router.post(
    "",
    response_model=ApiResponse[DownloaderView],
    summary="添加一个下载器（保存后异步测试连接）",
)
async def create_downloader_config(
    payload: DownloaderPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloaderView]:
    """保存下载器连接信息（状态置 pending），并在后台异步测试连通性。

    接口立即返回，前端可轮询 GET /downloaders/{id} 观察 status 变化：
    pending → verifying → active（可用）/ failed（见 last_error）。
    """
    service = DownloaderConfigService(session)
    row = await service.create(
        name=payload.name,
        client_type=payload.client_type,
        url=payload.url,
        username=payload.username,
        password=payload.password,
        save_path=payload.save_path,
        enabled=payload.enabled,
    )
    assert row.id is not None  # 落库后必有主键
    row = await service.start_verification(row.id)
    background_tasks.add_task(verify_downloader, row.id)
    return ok(DownloaderView.from_model(row), message="已保存，正在测试连接")


@router.put(
    "/{downloader_id}",
    response_model=ApiResponse[DownloaderView],
    summary="更新下载器配置（更新后重新测试连接）",
)
async def update_downloader_config(
    downloader_id: int,
    payload: DownloaderPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloaderView]:
    service = DownloaderConfigService(session)
    await service.update(
        downloader_id,
        name=payload.name,
        client_type=payload.client_type,
        url=payload.url,
        username=payload.username,
        password=payload.password,
        save_path=payload.save_path,
        enabled=payload.enabled,
    )
    row = await service.start_verification(downloader_id)
    background_tasks.add_task(verify_downloader, downloader_id)
    return ok(DownloaderView.from_model(row), message="已更新，正在测试连接")


@router.patch(
    "/{downloader_id}/status",
    response_model=ApiResponse[DownloaderView],
    summary="启用 / 停用下载器",
)
async def set_downloader_status(
    downloader_id: int,
    payload: DownloaderStatusUpdate,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloaderView]:
    service = DownloaderConfigService(session)
    row = await service.set_enabled(downloader_id, payload.enabled)
    return ok(DownloaderView.from_model(row))


@router.post(
    "/{downloader_id}/default",
    response_model=ApiResponse[DownloaderView],
    summary="设为默认下载器",
)
async def set_default_downloader(
    downloader_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloaderView]:
    """把该下载器设为默认（一键下载不选目标时投给它），同时取消其他台的默认。

    注意：其他记录的 is_default 也会随之变化，前端应整体刷新列表。"""
    service = DownloaderConfigService(session)
    row = await service.set_default(downloader_id)
    return ok(DownloaderView.from_model(row), message="已设为默认下载器")


@router.post(
    "/{downloader_id}/verify",
    response_model=ApiResponse[DownloaderView],
    summary="手动重新测试连接",
)
async def reverify_downloader(
    downloader_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[DownloaderView]:
    """手动触发一次连接测试（如下载器重启后想确认恢复）。

    行为：不存在 → 404；已在测试中 → 409；否则同步占位为 VERIFYING
    并在后台重新测试。"""
    service = DownloaderConfigService(session)
    row = await service.start_verification(downloader_id)
    background_tasks.add_task(verify_downloader, downloader_id)
    return ok(DownloaderView.from_model(row), message="已重新发起连接测试")


@router.delete(
    "/{downloader_id}",
    response_model=ApiResponse[dict],
    summary="删除下载器配置",
)
async def delete_downloader_config(
    downloader_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = DownloaderConfigService(session)
    await service.delete(downloader_id)
    return ok({}, message="已删除")
