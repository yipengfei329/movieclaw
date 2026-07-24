from __future__ import annotations

from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_api.exceptions import BadRequestException, ConflictException, NotFoundException
from movieclaw_api.schemas.library import (
    ClaimPayload,
    LastOrganizeView,
    LastScanView,
    LibraryItemView,
    LibraryPayload,
    LibraryStats,
    LibraryView,
    MissingClearPayload,
    MissingFileView,
    MissingItemView,
    OrganizePreviewView,
    OrganizeRenameView,
    OrganizeSidecarView,
    OrganizeSkipView,
    OrganizeStartView,
    RedownloadPayload,
    ScanProgressView,
    ScanResultView,
    UnidentifiedClearPayload,
    UnidentifiedFileView,
    derive_air_status,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.library_config import LibraryConfigService
from movieclaw_api.services.library_organize import (
    build_organize_plan,
    is_organizing,
    last_organize,
    organize_library,
    organize_progress,
)
from movieclaw_api.services.library_scan import (
    is_scanning,
    last_scan,
    request_stop_scan,
    scan_library,
    scan_progress,
)
from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_db.engine import get_session
from movieclaw_db.models import LibraryFile, MediaItem, MediaSeason, utcnow
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
        deferred=summary.deferred,
        cancelled=summary.cancelled,
        errors=list(summary.errors),
    )


def _organize_progress_view(library_id: int) -> ScanProgressView | None:
    """进行中整理的实时进度；没在整理返回 None。"""
    progress = organize_progress(library_id)
    if progress is None:
        return None
    processed, total = progress
    return ScanProgressView(processed=processed, total=total)


def _last_organize_view(library_id: int) -> LastOrganizeView | None:
    """把进程内的最近整理记录转成接口视图；没整理过返回 None。"""
    record = last_organize(library_id)
    if record is None:
        return None
    finished_at, summary = record
    return LastOrganizeView(
        finished_at=finished_at,
        renamed=summary.renamed,
        sidecars_renamed=summary.sidecars_renamed,
        already_ok=summary.already_ok,
        skipped=summary.skipped,
        removed_dirs=summary.removed_dirs,
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
                organizing=is_organizing(r.id or -1),
                organize_progress=_organize_progress_view(r.id or -1),
                last_organize=_last_organize_view(r.id or -1),
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
                reason=r.unidentified_reason,
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
            organizing=is_organizing(library_id),
            organize_progress=_organize_progress_view(library_id),
            last_organize=_last_organize_view(library_id),
        )
    )


@router.post(
    "",
    response_model=ApiResponse[LibraryView],
    summary="创建媒体库（该类型首个库自动成为默认，并自动开始首次扫描）",
)
async def create_library(
    payload: LibraryPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LibraryView]:
    service = LibraryConfigService(session)
    row = await service.create(name=payload.name, kind=payload.kind, root_paths=payload.root_paths)
    # 建库即扫描：根路径下的存量文件立刻开始识别入账，不用用户再手动点一次
    assert row.id is not None
    background_tasks.add_task(scan_library, row.id)
    return ok(
        LibraryView.from_model(row, scanning=True),
        message=f"已创建媒体库「{row.name}」，正在扫描存量文件",
    )


def _assert_not_busy(library_name: str, library_id: int) -> None:
    """扫描/整理期间锁定库的编辑与删除——两个任务都在按当前根路径批量
    读写台账，此刻改根路径或删库会让进行中的任务写入过期配置。"""
    if is_scanning(library_id):
        raise ConflictException(
            f"「{library_name}」正在扫描中，暂不能编辑或删除；请先停止扫描或等待完成"
        )
    if is_organizing(library_id):
        raise ConflictException(
            f"「{library_name}」正在整理文件名，暂不能编辑或删除；请等待整理完成"
        )


@router.put(
    "/{library_id}",
    response_model=ApiResponse[LibraryView],
    summary="更新媒体库（名称与根路径；类型创建后不可改；扫描/整理中锁定）",
)
async def update_library(
    library_id: int,
    payload: LibraryPayload,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[LibraryView]:
    service = LibraryConfigService(session)
    before = await service.get(library_id)
    _assert_not_busy(before.name, library_id)
    roots_changed = list(before.root_paths) != [p.strip() for p in payload.root_paths if p.strip()]
    row = await service.update(library_id, name=payload.name, root_paths=payload.root_paths)
    # 根路径变了就自动补扫：新目录的存量立刻入账，移除目录下的文件标记 missing
    if roots_changed:
        background_tasks.add_task(scan_library, library_id)
        return ok(
            LibraryView.from_model(row, scanning=True),
            message="已更新，正在按新的根路径重新扫描",
        )
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
    summary="删除媒体库（不动磁盘文件；其订阅回落到该类型默认库；扫描/整理中锁定）",
)
async def delete_library(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = LibraryConfigService(session)
    row = await service.get(library_id)
    _assert_not_busy(row.name, library_id)
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
    if is_organizing(library_id):
        raise ConflictException(f"「{library.name}」正在整理文件名，请等待整理完成后再扫描")
    background_tasks.add_task(scan_library, library_id)
    return ok(
        ScanResultView(started=True, message=f"已开始扫描「{library.name}」"),
        message=f"已开始扫描「{library.name}」，完成后库存自动更新",
    )


@router.post(
    "/{library_id}/scan/stop",
    response_model=ApiResponse[dict],
    summary="停止进行中的扫描（已入账的保留，剩余文件下次扫描继续）",
)
async def stop_scan(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    service = LibraryConfigService(session)
    library = await service.get(library_id)
    if not request_stop_scan(library_id):
        raise ConflictException(f"「{library.name}」当前没有进行中的扫描")
    return ok({}, message=f"正在停止「{library.name}」的扫描（当前文件处理完即停下）")


# ---------------------------------------------------------------------------
# 整理（存量规范化）：预览 / 执行
# ---------------------------------------------------------------------------


@router.post(
    "/{library_id}/organize/preview",
    response_model=ApiResponse[OrganizePreviewView],
    summary="预览整理计划：每个文件改成什么名、哪些跳过及原因（只读，不动磁盘）",
)
async def preview_organize(
    library_id: int,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[OrganizePreviewView]:
    """按刮削结果计算规范命名计划。纯只读——真正执行前用户在前端逐条
    确认；执行接口会重新计算计划，预览与执行之间的磁盘变化不会造成误改。"""
    service = LibraryConfigService(session)
    library = await service.get(library_id)
    plan = await build_organize_plan(session, library)
    return ok(
        OrganizePreviewView(
            total=plan.total,
            already_ok=plan.already_ok,
            renames=[
                OrganizeRenameView(
                    file_id=a.file_id,
                    media_item_id=a.media_item_id,
                    title=a.title,
                    year=a.year,
                    source_path=a.source_path,
                    target_path=a.target_path,
                    source_rel=a.source_rel,
                    target_rel=a.target_rel,
                    size_bytes=a.size_bytes,
                    sidecars=[
                        OrganizeSidecarView(source_path=s.source_path, target_path=s.target_path)
                        for s in a.sidecars
                    ],
                )
                for a in plan.renames
            ],
            skips=[OrganizeSkipView(file_path=s.file_path, reason=s.reason) for s in plan.skips],
        )
    )


@router.post(
    "/{library_id}/organize",
    response_model=ApiResponse[OrganizeStartView],
    summary="开始整理：按规范命名批量改名归位（后台执行，与扫描互斥）",
)
async def start_organize(
    library_id: int,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[OrganizeStartView]:
    """执行时重新计算计划并逐文件「改名 → 台账随迁」。改名直接发生在
    磁盘上、无法一键撤销——前端必须先展示预览并取得用户确认再调用。"""
    service = LibraryConfigService(session)
    library = await service.get(library_id)
    if is_organizing(library_id):
        raise ConflictException(f"「{library.name}」正在整理中，请等待完成")
    if is_scanning(library_id):
        raise ConflictException(f"「{library.name}」正在扫描中，请等待扫描完成后再整理")
    background_tasks.add_task(organize_library, library_id)
    return ok(
        OrganizeStartView(started=True, message=f"已开始整理「{library.name}」"),
        message=f"已开始整理「{library.name}」，完成后文件名将符合规范",
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

    # 剧集的已播单元集合：海报悬浮操作（订阅追新/补齐缺集）的判断依据。
    # 季集结构在条目建档时已落库（media_season），一次批量查询本地即得，
    # 不打 TMDB。特别季不参与缺集统计——订阅默认也不追特别季，口径一致。
    tv_item_ids = [i for i, (item, _) in grouped.items() if item.kind == "tv"]
    aired_by_item: dict[int, set[tuple[int, int]]] = {}
    if tv_item_ids:
        season_rows = (
            (
                await session.execute(
                    select(MediaSeason).where(MediaSeason.media_item_id.in_(tv_item_ids))  # type: ignore[attr-defined]
                )
            )
            .scalars()
            .all()
        )
        today = utcnow().date()
        for season in season_rows:
            if season.season_number == 0:
                continue
            aired = aired_by_item.setdefault(season.media_item_id, set())
            for episode in season.episodes:
                number = episode.get("episode_number")
                raw = episode.get("air_date")
                try:
                    if number is not None and raw and date.fromisoformat(raw) <= today:
                        aired.add((season.season_number, number))
                except ValueError:
                    continue

    base = get_settings().tmdb_image_base_url.rstrip("/")
    views = []
    for item, files in grouped.values():
        units = {(f.season_number, f.episode_number) for f in files}
        if item.kind == "tv":
            # 缺集口径与订阅创建的 E−H 一致：已播 − 在位（missing 的文件不算拥有），
            # 因此「补齐缺集」建订阅后恰好只为这些集生成工单
            present = {
                (f.season_number, f.episode_number) for f in files if f.missing_since is None
            }
            missing_episodes = len(aired_by_item.get(item.id, set()) - present)  # type: ignore[arg-type]
        else:
            missing_episodes = 0
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
                air_status=derive_air_status(item.status) if item.kind == "tv" else None,
                missing_episode_count=missing_episodes,
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
    subscriptions = SubscriptionService(session, MediaLibraryService(session, get_tmdb_client()))
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
    # 库存对账：认领让单元"在库"成立，关闭对应的订阅工单
    from movieclaw_api.services.wanted_fulfillment import close_fulfilled_wanted

    await close_fulfilled_wanted(session, item.id)
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
