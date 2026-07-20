"""存量扫描器（媒体库 L3 核心）：把库根路径下已有的文件识别并纳入台账。

识别链（docs/design/library.md M2，每个视频文件依次尝试）：
  ① NFO 优先——存量目录常有 Emby/TMM 刮削好的 NFO（内含 tmdb id），
     读到即免费精确身份；
  ② 文件名/目录名解析（enrich 复用）→ TMDB 标题+年份保守收敛
     （唯一命中才认，规则同豆瓣入口的 resolve）；
  ③ 仍无法确认 → 照样落账但 media_item_id=NULL，进"待识别"清单人工认领
     ——宁可待确认，不静默错挂。

增量语义：已在台账且在位的路径直接跳过（重扫秒级）；标记过 missing 的
文件再次被发现时自动清除标记。扫描绝不移动/重命名/删除任何存量文件。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from movieclaw_api.services.library_import import VIDEO_EXTS
from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.media_probe import probe_media
from movieclaw_db.engine import get_database
from movieclaw_db.models import FileSource, Library, LibraryFile, MediaItem, utcnow
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository
from movieclaw_enrich import enrich
from movieclaw_media.library import ResolveStatus, resolve_douban_to_tmdb
from movieclaw_media.models import MediaKind
from movieclaw_scheduler.registry import register_task

logger = logging.getLogger("movieclaw_api.library_scan")

# 目录级忽略（Emby/群晖/TMM 生态的辅助目录，moviebot 同款清单）
_IGNORE_DIRS = {"@eadir", ".deletedbytmm", "bonus", "extras", "sample", "samples"}
# 文件名含这些标记不入库
_IGNORE_MARKERS = ("sample",)

# 季目录名解析："Season 02" / "S02" / "Specials" / "特别篇"
_SEASON_DIR = re.compile(r"^(?:season[ ._-]*(\d{1,3})|s(\d{1,3}))$", re.IGNORECASE)
_SPECIALS_DIR = re.compile(r"^(?:specials?|特别篇|特典)$", re.IGNORECASE)

# NFO 里的 tmdb id：<tmdbid>123</tmdbid> 或 <uniqueid type="tmdb">123</uniqueid>
_NFO_TMDBID = re.compile(r"<tmdbid>\s*(\d+)\s*</tmdbid>", re.IGNORECASE)
_NFO_UNIQUEID = re.compile(r'<uniqueid[^>]*type="tmdb"[^>]*>\s*(\d+)\s*</uniqueid>', re.IGNORECASE)

# 同一时间每个库只允许一个扫描在跑（进程内互斥）
_scanning: set[int] = set()


@dataclass
class ScanSummary:
    """一次扫描的结论（日志与接口响应共用）。"""

    library_id: int
    scanned: int = 0  # 本轮处理的新文件数
    identified: int = 0  # 成功挂上身份锚
    unidentified: int = 0  # 落账但待识别
    skipped_known: int = 0  # 已在台账、直接跳过
    errors: list[str] = field(default_factory=list)


def is_scanning(library_id: int) -> bool:
    return library_id in _scanning


async def scan_library(library_id: int) -> ScanSummary:
    """扫描一个库的全部根路径（后台任务入口；自开会话，不向外抛异常）。"""
    summary = ScanSummary(library_id=library_id)
    if library_id in _scanning:
        summary.errors.append("该库已有扫描在进行中")
        return summary
    _scanning.add(library_id)
    try:
        return await _scan(library_id, summary)
    except Exception:  # noqa: BLE001 -- 后台任务兜底
        logger.exception("媒体库 #%s 扫描时发生未知错误", library_id)
        summary.errors.append("扫描中断：发生未知错误（详见后端日志）")
        return summary
    finally:
        _scanning.discard(library_id)


async def _scan(library_id: int, summary: ScanSummary) -> ScanSummary:
    db = get_database()
    async with db.session() as session:
        library = await session.get(Library, library_id)
        if library is None:
            summary.errors.append("媒体库不存在（可能已被删除）")
            return summary
        repo = LibraryFileRepository(session)
        known = {row.file_path: row for row in await repo.list_by_library(library_id)}
        media_service = MediaLibraryService(session, get_tmdb_client())
        kind = MediaKind(library.kind)
        # 每轮扫描内的收敛缓存：同一部剧几十集只查一次 TMDB
        resolve_cache: dict[tuple[str, int | None], MediaItem | None] = {}

        for root in library.root_paths:
            root_path = Path(root)
            if not root_path.exists():
                summary.errors.append(f"根路径不存在，已跳过：{root}")
                continue
            for file, is_disc in _walk_videos(root_path):
                path_str = str(file)
                existing = known.get(path_str)
                if existing is not None and existing.missing_since is None:
                    summary.skipped_known += 1
                    continue
                try:
                    await _ingest_file(
                        session,
                        repo,
                        media_service,
                        library,
                        kind,
                        root_path,
                        file,
                        resolve_cache,
                        summary,
                        is_disc=is_disc,
                    )
                except Exception as exc:  # noqa: BLE001 -- 单文件失败不断整轮
                    logger.exception("扫描文件失败：%s", file)
                    summary.errors.append(f"「{file.name}」处理失败：{exc}")

    logger.info(
        "媒体库 #%s 扫描完成：新入账 %d（已识别 %d / 待识别 %d），跳过已知 %d，问题 %d",
        library_id,
        summary.scanned,
        summary.identified,
        summary.unidentified,
        summary.skipped_known,
        len(summary.errors),
    )
    return summary


def _is_disc_dir(directory: Path) -> bool:
    """原盘目录判定：蓝光（BDMV）或 DVD（VIDEO_TS）结构。"""
    return (directory / "BDMV").is_dir() or (directory / "VIDEO_TS").is_dir()


def disc_main_stream(disc_dir: Path) -> Path | None:
    """原盘的主流文件：BDMV/STREAM 或 VIDEO_TS 下最大的流文件（探测用）。"""
    for sub, exts in (("BDMV/STREAM", {".m2ts"}), ("VIDEO_TS", {".vob"})):
        stream_dir = disc_dir / sub
        if not stream_dir.is_dir():
            continue
        candidates = [f for f in stream_dir.iterdir() if f.is_file() and f.suffix.lower() in exts]
        if candidates:
            return max(candidates, key=lambda f: f.stat().st_size)
    return None


def _disc_total_size(disc_dir: Path) -> int:
    total = 0
    for f in disc_dir.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            continue
    return total


def _walk_videos(root: Path):
    """深度遍历，产出 (路径, 是否原盘目录)。

    原盘目录（BDMV/VIDEO_TS 结构）整体作为**一个条目**产出、不再下钻——
    盘内的几十个流文件不是独立影片。普通目录剪掉忽略/隐藏目录后继续下钻。
    """
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if entry.is_dir():
                if name.startswith(".") or name.lower() in _IGNORE_DIRS:
                    continue
                if _is_disc_dir(entry):
                    yield entry, True
                    continue
                stack.append(entry)
                continue
            lower = name.lower()
            suffix = Path(lower).suffix
            if suffix not in VIDEO_EXTS and suffix != ".iso":
                continue
            if any(marker in lower for marker in _IGNORE_MARKERS):
                continue
            yield entry, False


async def _ingest_file(
    session,
    repo: LibraryFileRepository,
    media_service: MediaLibraryService,
    library: Library,
    kind: MediaKind,
    root: Path,
    file: Path,
    resolve_cache: dict,
    summary: ScanSummary,
    *,
    is_disc: bool = False,
) -> None:
    summary.scanned += 1
    # 先探测：实测时长是电影同名候选消歧的强信号（原盘探测其主流文件）
    probe_target = disc_main_stream(file) if is_disc else file
    spec = await asyncio.to_thread(probe_media, probe_target) if probe_target is not None else None
    item = await _identify(
        media_service,
        kind,
        root,
        file,
        resolve_cache,
        duration_seconds=spec.duration_seconds if spec else None,
    )
    season, episode = (0, 0) if is_disc else _unit_for(kind, file)
    attrs = enrich(file.stem if not is_disc else file.name)
    if is_disc:
        size_bytes = await asyncio.to_thread(_disc_total_size, file)
        container = "bluray" if (file / "BDMV").is_dir() else "dvd"
    else:
        size_bytes = file.stat().st_size
        container = file.suffix.lstrip(".").lower() or None
    assert library.id is not None
    await repo.upsert_by_path(
        LibraryFile(
            library_id=library.id,
            media_item_id=item.id if item else None,
            season_number=season,
            episode_number=episode,
            file_path=str(file),
            size_bytes=size_bytes,
            container=container,
            resolution=spec.resolution if spec else None,
            video_codec=spec.video_codec if spec else None,
            hdr=spec.hdr if spec else None,
            bit_depth=spec.bit_depth if spec else None,
            duration_seconds=spec.duration_seconds if spec else None,
            bit_rate=spec.bit_rate if spec else None,
            media_source=attrs.media_source,
            release_group=attrs.release_group,
            source=FileSource.SCANNED,
        )
    )
    if item is not None:
        summary.identified += 1
    else:
        summary.unidentified += 1


# ---------------------------------------------------------------------------
# 身份识别
# ---------------------------------------------------------------------------


async def _identify(
    media_service: MediaLibraryService,
    kind: MediaKind,
    root: Path,
    file: Path,
    cache: dict,
    *,
    duration_seconds: int | None = None,
) -> MediaItem | None:
    # ① NFO 精确身份
    tmdb_id = _nfo_tmdb_id(root, file)
    if tmdb_id is not None:
        try:
            return await media_service.ensure_media_item(kind, tmdb_id)
        except Exception as exc:  # noqa: BLE001 -- NFO 的 id 可能已失效，降级到解析
            logger.warning("NFO 指向的 TMDB 条目建档失败（id=%s）：%s", tmdb_id, exc)

    # ② 名称解析 → TMDB 保守收敛（电影歧义时用实测时长消歧）
    title, year = _guess_title(kind, root, file)
    if not title:
        return None
    key = (title, year)
    if key in cache:
        return cache[key]
    try:
        resolution, item = await _resolve(
            media_service, kind, title, year, duration_seconds=duration_seconds
        )
    except Exception as exc:  # noqa: BLE001 -- TMDB 波动不该中断扫描
        logger.warning("TMDB 收敛失败（%s / %s）：%s", title, year, exc)
        return None
    if resolution is not ResolveStatus.MATCHED:
        item = None
    cache[key] = item
    return item


async def _resolve(
    media_service: MediaLibraryService,
    kind: MediaKind,
    title: str,
    year: int | None,
    *,
    duration_seconds: int | None = None,
) -> tuple[ResolveStatus, MediaItem | None]:
    """标题+年份 → TMDB 锚，但比豆瓣入口**更保守**。

    豆瓣入口的查询词是用户输入的真实片名，"搜索结果唯一即命中"够用；
    扫描器的查询词来自任意文件/目录名（可能是"杂物"这类噪声），TMDB 的
    模糊搜索对噪声词也可能返回唯一但错误的结果。因此追加验收：
    **没有年份佐证时，命中条目的标题/原名/别名必须与查询词精确相等**，
    否则宁可进待识别清单（不静默错挂铁律）。
    """
    resolution = await resolve_douban_to_tmdb(get_tmdb_client(), kind, title, year=year)
    if resolution.status is ResolveStatus.AMBIGUOUS and (
        kind is MediaKind.MOVIE and duration_seconds
    ):
        # 时长消歧（文件识别独有的强信号）：实测时长 × 候选 runtime，
        # ±2 分钟内唯一命中才认——多个都接近仍视为歧义
        picked = await _pick_by_runtime(resolution.candidates, duration_seconds)
        if picked is not None:
            item = await media_service.ensure_media_item(kind, picked)
            logger.info(
                "时长消歧命中：「%s」（%d 分钟）→《%s》",
                title,
                duration_seconds // 60,
                item.title,
            )
            return ResolveStatus.MATCHED, item
    if resolution.status is not ResolveStatus.MATCHED:
        return resolution.status, None
    assert resolution.tmdb_id is not None
    item = await media_service.ensure_media_item(kind, resolution.tmdb_id, extra_aliases=[])
    if year is None and not _title_matches(title, item):
        logger.info(
            "扫描收敛被否决：查询「%s」命中《%s》但标题不符且无年份佐证，进待识别",
            title,
            item.title,
        )
        return ResolveStatus.AMBIGUOUS, None
    return ResolveStatus.MATCHED, item


# 时长消歧参数：±2 分钟容差；最多查前 5 个候选的详情（限流友好）
_RUNTIME_TOLERANCE_SECONDS = 120
_RUNTIME_CANDIDATES_MAX = 5


async def _pick_by_runtime(candidates, duration_seconds: int) -> int | None:
    """逐候选拉 TMDB runtime，与实测时长比对；唯一落在容差内的候选胜出。"""
    client = get_tmdb_client()
    hits: list[int] = []
    for candidate in candidates[:_RUNTIME_CANDIDATES_MAX]:
        try:
            detail = await client.get(f"movie/{candidate.tmdb_id}", {})
        except Exception:  # noqa: BLE001 -- 单候选拉取失败按不命中处理
            continue
        runtime = detail.get("runtime")
        if not runtime:
            continue
        if abs(runtime * 60 - duration_seconds) <= _RUNTIME_TOLERANCE_SECONDS:
            hits.append(candidate.tmdb_id)
    return hits[0] if len(hits) == 1 else None


def _title_matches(query: str, item: MediaItem) -> bool:
    """查询词与条目名精确相等（大小写不敏感；主名/原名/别名任一即可）。"""
    normalized = query.strip().casefold()
    names = [item.title, item.original_title, *item.aliases]
    return any(normalized == (name or "").strip().casefold() for name in names)


def _nfo_tmdb_id(root: Path, file: Path) -> int | None:
    """从 NFO 里读 tmdb id：同名 .nfo → 目录级 movie/tvshow.nfo（向上到库根）。"""
    candidates = [file.with_suffix(".nfo")]
    current = file.parent
    while True:
        candidates.append(current / "movie.nfo")
        candidates.append(current / "tvshow.nfo")
        if current == root or current.parent == current:
            break
        current = current.parent
    for nfo in candidates:
        if not nfo.is_file():
            continue
        try:
            text = nfo.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = _NFO_TMDBID.search(text) or _NFO_UNIQUEID.search(text)
        if match:
            return int(match.group(1))
    return None


def _guess_title(kind: MediaKind, root: Path, file: Path) -> tuple[str | None, int | None]:
    """猜条目名：剧集优先用"剧集目录名"（比文件名干净），电影优先用文件名。

    目录结构 ``{root}/{条目目录}[/Season NN]/文件`` 中，条目目录是紧挨
    库根的那一层；散落在库根下的裸文件退回用文件名解析。
    """
    entry_dir = _entry_dir(root, file)
    sources = []
    if kind is MediaKind.TV:
        if entry_dir is not None:
            sources.append(entry_dir.name)
        sources.append(file.stem)
    else:
        sources.append(file.stem)
        if entry_dir is not None:
            sources.append(entry_dir.name)
    for text in sources:
        attrs = enrich(text)
        title = (attrs.titles_zh[0] if attrs.titles_zh else None) or (
            attrs.titles_en[0] if attrs.titles_en else None
        )
        if title:
            return title, attrs.year
        # enrich 抽不出时退回"Title (Year)"目录名惯例
        plain = re.match(r"^(.+?)\s*\((\d{4})\)$", text.strip())
        if plain:
            return plain.group(1).strip(), int(plain.group(2))
    return None, None


def _entry_dir(root: Path, file: Path) -> Path | None:
    """文件归属的条目目录（库根的直接子目录）；文件直接躺在根下时为 None。"""
    try:
        relative = file.relative_to(root)
    except ValueError:
        return None
    parts = relative.parts
    if len(parts) < 2:
        return None
    return root / parts[0]


def _unit_for(kind: MediaKind, file: Path) -> tuple[int, int]:
    """期望单元：电影 (0,0)；剧集从文件名解析集号、季号缺失看父目录。"""
    if kind is MediaKind.MOVIE:
        return 0, 0
    attrs = enrich(file.stem)
    episode = attrs.episodes[0] if attrs.episodes else 0
    season = attrs.seasons[0] if attrs.seasons else _season_from_dir(file.parent)
    return season if season is not None else 0, episode


def _season_from_dir(directory: Path) -> int | None:
    name = directory.name.strip()
    if _SPECIALS_DIR.match(name):
        return 0
    match = _SEASON_DIR.match(name)
    if match:
        return int(match.group(1) or match.group(2))
    return None


# ---------------------------------------------------------------------------
# 定期对账（L3.4）：missing 标记 + 新文件补扫
# ---------------------------------------------------------------------------

# 对账节奏：低频巡检即可（文件消失不是急事；新文件靠手动扫描/入库为主）
RECONCILE_INTERVAL_SECONDS = 6 * 3600


@register_task(
    "library_reconcile",
    title="媒体库对账",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=RECONCILE_INTERVAL_SECONDS,
    description=(
        "定期核对媒体库台账与磁盘：消失的文件标记 missing（不删记录），"
        "根路径下的新文件增量补扫入账。"
    ),
)
async def reconcile_libraries() -> None:
    db = get_database()
    async with db.session() as session:
        libraries = list((await session.execute(select(Library))).scalars().all())
    for library in libraries:
        assert library.id is not None
        await _reconcile_missing(library.id)
        # 新文件补扫（增量：已知路径直接跳过）
        summary = await scan_library(library.id)
        if summary.errors:
            logger.warning(
                "媒体库「%s」对账补扫存在问题：%s", library.name, "；".join(summary.errors)
            )


async def _reconcile_missing(library_id: int) -> None:
    """台账里的文件是否仍在位：消失 → 标记 missing_since（磁盘检查放线程池）。"""
    db = get_database()
    async with db.session() as session:
        repo = LibraryFileRepository(session)
        rows = await repo.list_by_library(library_id)
        now = utcnow()
        marked = 0
        for row in rows:
            exists = await asyncio.to_thread(Path(row.file_path).exists)
            if not exists and row.missing_since is None:
                assert row.id is not None
                await repo.mark_missing(row.id, since=now)
                marked += 1
        if marked:
            logger.warning(
                "媒体库 #%s 对账：%d 个文件已不在原位，已标记 missing（记录保留）",
                library_id,
                marked,
            )
