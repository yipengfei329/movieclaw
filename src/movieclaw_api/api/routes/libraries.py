from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_api.exceptions import BadRequestException, ConflictException, NotFoundException
from movieclaw_api.schemas.library import (
    ClaimPayload,
    LastScanView,
    LibraryItemView,
    LibraryPayload,
    LibraryStats,
    LibraryView,
    MissingClearPayload,
    MissingFileView,
    MissingItemView,
    RedownloadPayload,
    ScanProgressView,
    ScanResultView,
    UnidentifiedClearPayload,
    UnidentifiedFileView,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.library_config import LibraryConfigService
from movieclaw_api.services.library_scan import (
    is_scanning,
    last_scan,
    scan_library,
    scan_progress,
)
from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_db.engine import get_session
from movieclaw_db.models import LibraryFile, MediaItem
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository
from movieclaw_media.models import MediaKind

router = APIRouter(prefix="/libraries", tags=["libraries"])


def _scan_progress_view(library_id: int) -> ScanProgressView | None:
    """进行中扫描的实时进度；没在扫返回 None。"""
    progress = scan_progress(library_id)
    if progress is None:
        return None
    processed, total = progress
    return ScanProgressView(processed=processed, total=total)


def _last_scan_view(library_id: int) -> LastScanView | None:
    """把进程内的最近扫描记录转成接口视图；没扫过返回 None。"""
    record = last_scan(library_id)
    if record is None:
        return None
    finished_at, summary = record
    return LastScanView(
        finished_at=finished_at,
        scanned=summary.scanned,
        identified=summary.identified,
        unidentified=summary.unidentified,
        marked_missing=summary.marked_missing,
        errors=list(summary.errors),
    )


async def _stats_by_library(session: AsyncSession) -> dict[int, LibraryStats]:
    """全部库的库存统计（一次查询，Python 聚合——单机规模足够）。"""
    rows = list((await session.execute(select(LibraryFile))).scalars().all())
    stats: dict[int, LibraryStats] = {}
    items: dict[int, set[int]] = {}
    for row in rows:
        s = stats.setdefault(row.library_id, LibraryStats())
        s.file_count += 1
        s.total_size_bytes += row.size_bytes
        if row.missing_since is not None:
            s.missing_count += 1
        if row.media_item_id is None:
            s.unidentified_count += 1
        else:
            items.setdefault(row.library_id, set()).add(row.media_item_id)
    for library_id, media_ids in items.items():
        stats[library_id].item_count = len(media_ids)
    return stats


@router.get(
    "",
    response_model=ApiResponse[list[LibraryView]],
    summary="列出全部媒体库（含库存统计，可按类型过滤）",
)
async def list_libraries(
    kind: str | None = Query(default=None, description="movie / tv，缺省全部"),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[LibraryView]]:
    service = LibraryConfigService(session)
    rows = await service.list_all(kind=kind)
    stats = await _stats_by_library(session)
    return ok(
        [
            LibraryView.from_model(
                r,
                stats=stats.get(r.id or -1),
                scanning=is_scanning(r.id or -1),
                scan_progress=_scan_progress_view(r.id or -1),
                last_scan=_last_scan_view(r.id or -1),
            )
            for r in rows
        ]
    )


@router.get(
    "/unidentified",
    response_model=ApiResponse[list[UnidentifiedFileView]],
    summary="待识别清单（扫描后无法确认身份的文件，可按库过滤）",
)
async def list_unidentified(
    library_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[UnidentifiedFileView]]:
    repo = LibraryFileRepository(session)
    rows = await repo.list_unidentified(library_id=library_id)
    service = LibraryConfigService(session)
    names = {library.id: library.name for library in await service.list_all()}
    return ok(
        [
            UnidentifiedFileView(
                id=r.id,  # type: ignore[arg-type]
                library_id=r.library_id,
                library_name=names.get(r.library_id, "?"),
                file_path=r.file_path,
                size_bytes=r.size_bytes,
                season_number=r.season_number,
                episode_number=r.episode_number,
            )
            for r in rows
        ]
    )


@router.get(
    "/{library_id}",
    response_model=ApiResponse[LibraryView],
    summary="获取单个媒体库详情",
)
async def get_library(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LibraryView]:
    service = LibraryConfigService(session)
    return ok(
        LibraryView.from_model(
            await service.get(library_id),
            scanning=is_scanning(library_id),
            scan_progress=_scan_progress_view(library_id),
            last_scan=_last_scan_view(library_id),
        )
    )


@router.post(
    "",
    response_model=ApiResponse[LibraryView],
    summary="创建媒体库（该类型首个库自动成为默认）",
)
async def create_library(
    payload: LibraryPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LibraryView]:
    service = LibraryConfigService(session)
    row = await service.create(name=payload.name, kind=payload.kind, root_paths=payload.root_paths)
    return ok(LibraryView.from_model(row), message=f"已创建媒体库「{row.name}」")


@router.put(
    "/{library_id}",
    response_model=ApiResponse[LibraryView],
    summary="更新媒体库（名称与根路径；类型创建后不可改）",
)
async def update_library(
    library_id: int,
    payload: LibraryPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LibraryView]:
    service = LibraryConfigService(session)
    row = await service.update(library_id, name=payload.name, root_paths=payload.root_paths)
    return ok(LibraryView.from_model(row), message="已更新")


@router.post(
    "/{library_id}/default",
    response_model=ApiResponse[LibraryView],
    summary="设为该类型的默认库",
)
async def set_default_library(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LibraryView]:
    """把该库设为其类型的默认库（订阅/手动下载不选库时用它），
    同 kind 其他库的默认标记随之取消，前端应整体刷新列表。"""
    service = LibraryConfigService(session)
    row = await service.set_default(library_id)
    return ok(LibraryView.from_model(row), message=f"「{row.name}」已设为默认库")


@router.delete(
    "/{library_id}",
    response_model=ApiResponse[dict],
    summary="删除媒体库（不动磁盘文件；其订阅回落到该类型默认库）",
)
async def delete_library(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = LibraryConfigService(session)
    await service.delete(library_id)
    return ok({}, message="已删除（磁盘文件未受影响）")


# ---------------------------------------------------------------------------
# 库存（L3）：扫描 / 条目聚合 / 待识别认领
# ---------------------------------------------------------------------------


@router.post(
    "/{library_id}/scan",
    response_model=ApiResponse[ScanResultView],
    summary="扫描该库的根路径，把存量文件识别入账（后台执行）",
)
async def start_scan(
    library_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[ScanResultView]:
    """增量扫描：已在台账的文件秒过；新文件走 NFO → 文件名解析 → TMDB
    识别链，认不出的进「待识别」清单。扫描绝不移动/改名/删除存量文件。"""
    service = LibraryConfigService(session)
    library = await service.get(library_id)
    if is_scanning(library_id):
        raise ConflictException(f"「{library.name}」正在扫描中，请等待完成")
    background_tasks.add_task(scan_library, library_id)
    return ok(
        ScanResultView(started=True, message=f"已开始扫描「{library.name}」"),
        message=f"已开始扫描「{library.name}」，完成后库存自动更新",
    )


@router.get(
    "/{library_id}/items",
    response_model=ApiResponse[list[LibraryItemView]],
    summary="库内媒体条目的库存聚合（单库海报墙数据源）",
)
async def list_library_items(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[LibraryItemView]]:
    from movieclaw_api.core.config import get_settings

    service = LibraryConfigService(session)
    await service.get(library_id)  # 404 检查
    result = await session.execute(
        select(LibraryFile, MediaItem)
        .join(MediaItem, LibraryFile.media_item_id == MediaItem.id)  # type: ignore[arg-type]
        .where(LibraryFile.library_id == library_id)
    )
    grouped: dict[int, tuple[MediaItem, list[LibraryFile]]] = {}
    for file, item in result.all():
        assert item.id is not None
        grouped.setdefault(item.id, (item, []))[1].append(file)

    base = get_settings().tmdb_image_base_url.rstrip("/")
    views = []
    for item, files in grouped.values():
        units = {(f.season_number, f.episode_number) for f in files}
        views.append(
            LibraryItemView(
                media_item_id=item.id,  # type: ignore[arg-type]
                kind=MediaKind(item.kind),
                tmdb_id=item.tmdb_id,
                title=item.title,
                year=item.year,
                poster_url=f"{base}/w500{item.poster_path}" if item.poster_path else None,
                file_count=len(files),
                total_size_bytes=sum(f.size_bytes for f in files),
                seasons=sorted({s for s, _ in units if item.kind == "tv"}),
                episode_count=len(units) if item.kind == "tv" else 0,
                resolutions=sorted({f.resolution for f in files if f.resolution}, reverse=True),
                missing_count=sum(1 for f in files if f.missing_since is not None),
                added_at=max(f.created_at for f in files),
            )
        )
    views.sort(key=lambda v: v.title)
    return ok(views)


@router.get(
    "/{library_id}/missing",
    response_model=ApiResponse[list[MissingItemView]],
    summary="缺失清单：文件已不在磁盘的库存，按条目聚合",
)
async def list_missing(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[MissingItemView]]:
    from movieclaw_api.core.config import get_settings
    from movieclaw_db.models import Subscription

    service = LibraryConfigService(session)
    await service.get(library_id)  # 404 检查
    result = await session.execute(
        select(LibraryFile, MediaItem)
        .join(MediaItem, LibraryFile.media_item_id == MediaItem.id)  # type: ignore[arg-type]
        .where(
            LibraryFile.library_id == library_id,
            LibraryFile.missing_since.is_not(None),  # type: ignore[union-attr]
        )
    )
    grouped: dict[int, tuple[MediaItem, list[LibraryFile]]] = {}
    for file, item in result.all():
        assert item.id is not None
        grouped.setdefault(item.id, (item, []))[1].append(file)
    if not grouped:
        return ok([])

    # 有订阅的条目要标出来：清理记录后订阅可能把它重新下回来
    subs = await session.execute(
        select(Subscription).where(Subscription.media_item_id.in_(grouped.keys()))  # type: ignore[union-attr]
    )
    sub_by_item = {s.media_item_id: s.id for s in subs.scalars().all()}

    base = get_settings().tmdb_image_base_url.rstrip("/")
    views = [
        MissingItemView(
            media_item_id=item.id,  # type: ignore[arg-type]
            kind=MediaKind(item.kind),
            tmdb_id=item.tmdb_id,
            title=item.title,
            year=item.year,
            poster_url=f"{base}/w500{item.poster_path}" if item.poster_path else None,
            subscription_id=sub_by_item.get(item.id),
            files=[
                MissingFileView(
                    id=f.id,  # type: ignore[arg-type]
                    file_path=f.file_path,
                    season_number=f.season_number,
                    episode_number=f.episode_number,
                    size_bytes=f.size_bytes,
                )
                for f in sorted(files, key=lambda f: (f.season_number, f.episode_number))
            ],
        )
        for item, files in grouped.values()
    ]
    views.sort(key=lambda v: v.title)
    return ok(views)


@router.post(
    "/missing/clear",
    response_model=ApiResponse[dict],
    summary="清理缺失记录（只删台账，绝不动磁盘）；不带 media_item_id 清整库",
)
async def clear_missing(
    payload: MissingClearPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = LibraryConfigService(session)
    await service.get(payload.library_id)  # 404 检查
    conditions = [
        LibraryFile.library_id == payload.library_id,
        LibraryFile.missing_since.is_not(None),  # type: ignore[union-attr]
    ]
    if payload.media_item_id is not None:
        conditions.append(LibraryFile.media_item_id == payload.media_item_id)
    rows = list((await session.execute(select(LibraryFile).where(*conditions))).scalars().all())
    for row in rows:
        await session.delete(row)
    # 本项目约定：get_session 不自动提交，事务由业务层显式收口
    await session.commit()
    return ok({"cleared": len(rows)}, message=f"已清理 {len(rows)} 条缺失记录（磁盘未动）")


@router.post(
    "/unidentified/clear",
    response_model=ApiResponse[dict],
    summary="批量忽略整库的待识别文件（只删台账，绝不动磁盘）",
)
async def clear_unidentified(
    payload: UnidentifiedClearPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = LibraryConfigService(session)
    await service.get(payload.library_id)  # 404 检查
    rows = list(
        (
            await session.execute(
                select(LibraryFile).where(
                    LibraryFile.library_id == payload.library_id,
                    LibraryFile.media_item_id.is_(None),  # type: ignore[union-attr]
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await session.delete(row)
    # 本项目约定：get_session 不自动提交，事务由业务层显式收口
    await session.commit()
    return ok({"cleared": len(rows)}, message=f"已忽略 {len(rows)} 个待识别文件（磁盘未动）")


@router.post(
    "/missing/redownload",
    response_model=ApiResponse[dict],
    summary="重新下载缺失内容：缺失单元交回订阅管线（无订阅则按缺失季创建）",
)
async def redownload_missing(
    payload: RedownloadPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    from movieclaw_api.services.subscription import SubscriptionService

    service = LibraryConfigService(session)
    await service.get(payload.library_id)  # 404 检查
    item = await session.get(MediaItem, payload.media_item_id)
    if item is None:
        raise NotFoundException("媒体条目不存在")
    rows = list(
        (
            await session.execute(
                select(LibraryFile).where(
                    LibraryFile.library_id == payload.library_id,
                    LibraryFile.media_item_id == payload.media_item_id,
                    LibraryFile.missing_since.is_not(None),  # type: ignore[union-attr]
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise BadRequestException("该条目没有缺失文件")
    units = {(r.season_number, r.episode_number) for r in rows}
    subscriptions = SubscriptionService(
        session, MediaLibraryService(session, get_tmdb_client())
    )
    subscription, requeued = await subscriptions.redownload_missing_units(
        MediaKind(item.kind), item, units, library_id=payload.library_id
    )
    return ok(
        {"subscription_id": subscription.id, "requeued": requeued},
        message=f"《{item.title}》的 {len(units)} 个缺失单元已交给订阅管线补回",
    )


@router.post(
    "/files/{file_id}/claim",
    response_model=ApiResponse[dict],
    summary="认领待识别文件：挂到指定的 TMDB 条目",
)
async def claim_file(
    file_id: int,
    payload: ClaimPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    repo = LibraryFileRepository(session)
    row = await session.get(LibraryFile, file_id)
    if row is None:
        raise NotFoundException(f"台账记录不存在：id={file_id}")
    library = await LibraryConfigService(session).get(row.library_id)
    kind = MediaKind(library.kind)
    if kind is MediaKind.MOVIE and (payload.season_number or payload.episode_number):
        raise BadRequestException("电影文件不需要季集号")
    media_service = MediaLibraryService(session, get_tmdb_client())
    item = await media_service.ensure_media_item(kind, payload.tmdb_id)
    assert item.id is not None
    await repo.claim_identity(
        file_id,
        media_item_id=item.id,
        season_number=payload.season_number,
        episode_number=payload.episode_number,
    )
    return ok({}, message=f"已认领为《{item.title}》")


@router.delete(
    "/files/{file_id}",
    response_model=ApiResponse[dict],
    summary="从台账忽略一个待识别文件（不动磁盘）",
)
async def ignore_file(
    file_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    repo = LibraryFileRepository(session)
    if not await repo.delete(file_id):
        raise NotFoundException(f"台账记录不存在：id={file_id}")
    return ok({}, message="已从台账忽略（磁盘文件未受影响；重新扫描会再次发现它）")
