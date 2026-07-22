"""存量扫描器（媒体库 L3 核心）：把库根路径下已有的文件识别并纳入台账。

识别链（docs/design/library.md M2，每个视频文件依次尝试）：
  ① NFO 优先——存量目录常有 Emby/TMM 刮削好的 NFO（内含 tmdb id），
     读到即免费精确身份；
  ② 文件名/目录名解析（enrich 复用）→ TMDB 证据验证收敛
     （标题门槛 + 年份/时长/季集数佐证，见 library_resolve 模块头注释）；
  ③ 仍无法确认 → 照样落账但 media_item_id=NULL，进"待识别"清单人工认领
     ——宁可待确认，不静默错挂。

增量语义：已在台账且在位的路径直接跳过（重扫秒级）；标记过 missing 的
文件再次被发现时自动清除标记。扫描同时感知删除：在位根路径下、台账有
但本轮没遍历到的文件标记 missing（挂载失败的根整个跳过、不误伤）——
"扫描 = 把台账与磁盘对齐"，新增与消失一次看全。扫描绝不移动/重命名/
删除任何存量文件，missing 只是标记、记录永远保留。

改名归并：磁盘上被改名/移动的文件（旧路径消失 + 新路径出现）在落账前
用"尺寸 + 时长"指纹匹配旧行，唯一命中即整行随迁——身份锚（含人工
认领）无损延续，不产生幽灵 missing 行（见 _try_relink）。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from movieclaw_api.services.library_import import VIDEO_EXTS
from movieclaw_api.services.library_resolve import (
    LocalEvidence,
    parse_total_episodes,
    verify_resolve,
)
from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.media_probe import probe_media
from movieclaw_db.engine import get_database
from movieclaw_db.models import DownloadHint, FileSource, Library, LibraryFile, MediaItem, utcnow
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository
from movieclaw_enrich import enrich
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
# 每库最近一次扫描的结论（进程内即可：给前端"扫描完成了什么"的反馈——
# 扫描常常毫秒级结束，没有这份记录用户会以为点了没反应）
_last_scans: dict[int, tuple] = {}
# 扫描进行中的实时进度 (已处理, 总数)：前端轮询后在库封面上画进度环
_progress: dict[int, tuple[int, int]] = {}


@dataclass
class ScanSummary:
    """一次扫描的结论（日志与接口响应共用）。"""

    library_id: int
    scanned: int = 0  # 本轮处理的新文件数
    identified: int = 0  # 成功挂上身份锚
    unidentified: int = 0  # 落账但待识别
    relinked: int = 0  # 改名归并：旧台账行迁到新路径（身份延续，不算新入账）
    skipped_known: int = 0  # 已在台账、直接跳过
    marked_missing: int = 0  # 台账有但磁盘上已消失，标记 missing
    errors: list[str] = field(default_factory=list)


def is_scanning(library_id: int) -> bool:
    return library_id in _scanning


def last_scan(library_id: int) -> tuple | None:
    """最近一次扫描的 (完成时间, ScanSummary)；该库从未扫描过则为 None。"""
    return _last_scans.get(library_id)


def scan_progress(library_id: int) -> tuple[int, int] | None:
    """进行中扫描的 (已处理, 总数)；没有扫描在跑则为 None。"""
    return _progress.get(library_id)


async def scan_library(library_id: int) -> ScanSummary:
    """扫描一个库的全部根路径（后台任务入口；自开会话，不向外抛异常）。"""
    from movieclaw_api.services.library_organize import is_organizing

    summary = ScanSummary(library_id=library_id)
    if library_id in _scanning:
        summary.errors.append("该库已有扫描在进行中")
        return summary
    # 与整理互斥：整理在批量改名，扫描此刻介入会把刚搬走的旧路径标 missing、
    # 把新路径当新文件重走识别链（人工认领可能丢失）。手动扫描、watchdog
    # 去抖、6 小时对账三个入口都收敛到这里，统一挡下（整理中台账已同步
    # 更新，无需扫描补账，漏掉的变更由下轮对账兜底）
    if is_organizing(library_id):
        summary.errors.append("该库正在整理文件名，扫描已跳过（整理完成后可重新扫描）")
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
        _progress.pop(library_id, None)
        _last_scans[library_id] = (utcnow(), summary)


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
        # 每轮扫描内的收敛缓存：同一部剧同一季几十集只查一次 TMDB
        resolve_cache: dict[tuple, MediaItem | None] = {}
        # 下载线索：手动下载提交时锚定的「条目目录 → 副标题」（拼音名种子的救赎）
        hints = await _load_hints(session)

        # 先盘点全部待处理文件（纯目录遍历、很快）：总数定下来，进度才有分母
        seen_paths: set[str] = set()
        scanned_roots: list[str] = []
        pending: list[tuple[Path, Path, bool]] = []  # (根, 文件, 是否原盘)
        for root in library.root_paths:
            root_path = Path(root)
            if not root_path.exists():
                summary.errors.append(f"根路径不存在，已跳过：{root}")
                continue
            scanned_roots.append(str(root_path))
            for file, is_disc in _walk_videos(root_path):
                seen_paths.add(str(file))
                pending.append((root_path, file, is_disc))

        assert library.id is not None
        _progress[library.id] = (0, len(pending))
        for done, (root_path, file, is_disc) in enumerate(pending, start=1):
            path_str = str(file)
            existing = known.get(path_str)
            if existing is not None and existing.missing_since is None:
                summary.skipped_known += 1
                _progress[library.id] = (done, len(pending))
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
                    hint=_hint_for(file, hints),
                )
            except Exception as exc:  # noqa: BLE001 -- 单文件失败不断整轮
                logger.exception("扫描文件失败：%s", file)
                summary.errors.append(f"「{file.name}」处理失败：{exc}")
            _progress[library.id] = (done, len(pending))

        # 收尾感知删除：在位根路径下、台账有但本轮没遍历到 → 标记 missing。
        # 不存在的根整个不参与（挂载失败/掉盘时不误伤），文件回归时
        # upsert_by_path 会自动清除标记。判定须读行上的当前路径而非快照
        # 的旧 key：改名归并（_try_relink）会把行迁到本轮刚遍历过的新路径
        prefixes = [f"{r.rstrip('/')}/" for r in scanned_roots]
        now = utcnow()
        for row in known.values():
            path_str = row.file_path
            if row.missing_since is not None or path_str in seen_paths:
                continue
            if not any(path_str.startswith(prefix) for prefix in prefixes):
                continue
            assert row.id is not None
            await repo.mark_missing(row.id, since=now)
            summary.marked_missing += 1

    if summary.marked_missing:
        logger.warning(
            "媒体库 #%s：%d 个文件已不在原位，已标记 missing（记录保留，文件回归自动恢复）",
            library_id,
            summary.marked_missing,
        )
    logger.info(
        "媒体库 #%s 扫描完成：新入账 %d（已识别 %d / 待识别 %d），"
        "改名归并 %d，跳过已知 %d，标记丢失 %d，问题 %d",
        library_id,
        summary.scanned - summary.relinked,
        summary.identified,
        summary.unidentified,
        summary.relinked,
        summary.skipped_known,
        summary.marked_missing,
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
    hint: _SubtitleHint | None = None,
) -> None:
    summary.scanned += 1
    # 先探测：实测时长是电影同名候选消歧的强信号（原盘探测其主流文件）
    probe_target = disc_main_stream(file) if is_disc else file
    spec = await asyncio.to_thread(probe_media, probe_target) if probe_target is not None else None
    if is_disc:
        size_bytes = await asyncio.to_thread(_disc_total_size, file)
        container = "bluray" if (file / "BDMV").is_dir() else "dvd"
    else:
        size_bytes = file.stat().st_size
        container = file.suffix.lstrip(".").lower() or None

    # 改名归并（走识别链之前）：新路径可能只是台账里某个旧文件被改了名，
    # 归并成功即结束——身份锚（含人工认领）随行迁移，免一次 TMDB 收敛
    if await _try_relink(
        repo,
        library,
        file,
        size_bytes=size_bytes,
        container=container,
        duration_seconds=spec.duration_seconds if spec else None,
    ):
        summary.relinked += 1
        return

    item = await _identify(
        media_service,
        kind,
        root,
        file,
        resolve_cache,
        duration_seconds=spec.duration_seconds if spec else None,
        hint=hint,
    )
    season, episode = (0, 0) if is_disc else _unit_for(kind, file)
    attrs = enrich(file.stem if not is_disc else file.name)
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
# 改名归并
# ---------------------------------------------------------------------------

# 时长互证容差：同一文件两次 ffprobe 结果应一致，留 2 秒余量防版本差异
_RELINK_DURATION_TOLERANCE_SECONDS = 2


async def _try_relink(
    repo: LibraryFileRepository,
    library: Library,
    file: Path,
    *,
    size_bytes: int,
    container: str | None,
    duration_seconds: int | None,
) -> bool:
    """新路径落账前，先找"已消失的同尺寸旧行"迁移过来（磁盘改名检测）。

    磁盘上的改名/移动对台账来说是"旧路径消失 + 新路径出现"两个独立事件，
    直接当新文件落账会丢掉旧行的身份锚（尤其是人工认领的成果），旧行则
    沦为永久 missing 的幽灵行。这里用改名的不变量做指纹：

    - 候选：同库、尺寸精确相等、且已标记 missing 或路径已不在磁盘
      （后者覆盖 watchdog 实时触发、6 小时对账还没跑的窗口期）；
      路径仍在磁盘的同尺寸行是复制/硬链，不是改名，不参与；
    - 互证：新旧双方都有实测时长时必须一致（±2 秒），一方缺失只凭尺寸；
    - **唯一命中才归并**——多个候选宁可当新文件走识别链，不静默错挂。
    """
    assert library.id is not None
    path_str = str(file)
    candidates = []
    for row in await repo.find_by_size(library.id, size_bytes):
        if row.file_path == path_str:
            continue
        if row.missing_since is None and await asyncio.to_thread(Path(row.file_path).exists):
            continue
        if (
            duration_seconds
            and row.duration_seconds
            and abs(duration_seconds - row.duration_seconds) > _RELINK_DURATION_TOLERANCE_SECONDS
        ):
            continue
        candidates.append(row)
    if len(candidates) != 1:
        return False
    old = candidates[0]
    assert old.id is not None
    old_path = old.file_path
    await repo.relocate(old.id, file_path=path_str, container=container)
    logger.info("检测到文件改名，台账已随迁（身份保留）：%s → %s", old_path, path_str)
    return True


# ---------------------------------------------------------------------------
# 身份识别
# ---------------------------------------------------------------------------


@dataclass
class _SubtitleHint:
    """``download_hint`` 行的解析形态（每轮扫描解析一次，同目录多文件复用）。"""

    save_path: str
    alt_title: str | None  # 副标题里的中文片名（enrich 提取）
    total_episodes: int | None  # 副标题「全N集」


async def _load_hints(session) -> list[_SubtitleHint]:
    """加载并解析全部下载线索；最长路径在前，嵌套目录时取最具体的一条。"""
    rows = list((await session.execute(select(DownloadHint))).scalars().all())
    hints = []
    for row in rows:
        attrs = enrich(row.subtitle)
        hints.append(
            _SubtitleHint(
                save_path=row.save_path.rstrip("/"),
                alt_title=attrs.titles_zh[0] if attrs.titles_zh else None,
                total_episodes=parse_total_episodes(row.subtitle),
            )
        )
    hints.sort(key=lambda h: len(h.save_path), reverse=True)
    return hints


def _hint_for(file: Path, hints: list[_SubtitleHint]) -> _SubtitleHint | None:
    """文件落在某条线索的目录之下 → 该线索适用（列表已按最具体优先排序）。"""
    for hint in hints:
        if Path(hint.save_path) in file.parents:
            return hint
    return None


async def _identify(
    media_service: MediaLibraryService,
    kind: MediaKind,
    root: Path,
    file: Path,
    cache: dict,
    *,
    duration_seconds: int | None = None,
    hint: _SubtitleHint | None = None,
) -> MediaItem | None:
    # ① NFO 精确身份
    tmdb_id = _nfo_tmdb_id(root, file)
    if tmdb_id is not None:
        try:
            return await media_service.ensure_media_item(kind, tmdb_id)
        except Exception as exc:  # noqa: BLE001 -- NFO 的 id 可能已失效，降级到解析
            logger.warning("NFO 指向的 TMDB 条目建档失败（id=%s）：%s", tmdb_id, exc)

    # ② 名称解析 → TMDB 证据验证收敛（年份/时长/季集数佐证见 library_resolve）
    evidence = _guess_evidence(kind, root, file)
    # 下载线索补强：副标题中文名作备选查询词，「全N集」作集数佐证；
    # 文件/目录名完全解析不出条目名时，中文名直接顶为主查询词
    if hint is not None:
        if evidence is None:
            if hint.alt_title:
                evidence = LocalEvidence(title=hint.alt_title)
        else:
            evidence.alt_title = hint.alt_title
        if evidence is not None:
            evidence.total_episodes = hint.total_episodes
    if evidence is None:
        return None
    evidence.duration_seconds = duration_seconds
    key = (evidence.title, evidence.alt_title, evidence.year, evidence.season)
    if key in cache:
        return cache[key]
    try:
        tmdb_id = await verify_resolve(get_tmdb_client(), kind, evidence)
        item = (
            await media_service.ensure_media_item(kind, tmdb_id, extra_aliases=[])
            if tmdb_id is not None
            else None
        )
    except Exception as exc:  # noqa: BLE001 -- TMDB 波动不该中断扫描
        logger.warning("TMDB 收敛失败（%s / %s）：%s", evidence.title, evidence.year, exc)
        return None
    cache[key] = item
    return item


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


def _guess_evidence(kind: MediaKind, root: Path, file: Path) -> LocalEvidence | None:
    """收集本地识别证据：条目名/年份 + 剧集的季集号（供收敛验证器佐证）。

    条目名：剧集优先用"剧集目录名"（比文件名干净），电影优先用文件名；
    目录结构 ``{root}/{条目目录}[/Season NN]/文件`` 中，条目目录是紧挨
    库根的那一层，散落在库根下的裸文件退回用文件名解析。
    季集号：目录名与文件名两个来源合并取最大（季包目录带 SNN、文件名带
    SxxExx，各有一半信息）；S00 特别篇不计入季数证据。
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
    parsed = [enrich(text) for text in sources]

    evidence: LocalEvidence | None = None
    for text, attrs in zip(sources, parsed, strict=True):
        title = (attrs.titles_zh[0] if attrs.titles_zh else None) or (
            attrs.titles_en[0] if attrs.titles_en else None
        )
        if title:
            evidence = LocalEvidence(title=title, year=attrs.year)
            break
        # enrich 抽不出时退回"Title (Year)"目录名惯例
        plain = re.match(r"^(.+?)\s*\((\d{4})\)$", text.strip())
        if plain:
            evidence = LocalEvidence(title=plain.group(1).strip(), year=int(plain.group(2)))
            break
    if evidence is None:
        return None
    if kind is MediaKind.TV:
        seasons = [s for attrs in parsed for s in attrs.seasons if s > 0]
        episodes = [e for attrs in parsed for e in attrs.episodes]
        evidence.season = max(seasons) if seasons else None
        evidence.episode = max(episodes) if episodes else None
    return evidence


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
# 定期对账（L3.4）：兜底巡检
# ---------------------------------------------------------------------------

# 对账节奏：低频兜底即可——新增/删除的即时感知靠手动扫描和目录监听
# （scan_library 本身已同时处理入账与 missing 标记），定时任务只兜底
# 监听失效（如网络挂载不产生 fs 事件）的场景
RECONCILE_INTERVAL_SECONDS = 6 * 3600


@register_task(
    "library_reconcile",
    title="媒体库对账",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=RECONCILE_INTERVAL_SECONDS,
    description=(
        "定期把媒体库台账与磁盘对齐：新文件增量入账，消失的文件标记 missing"
        "（不删记录）。兜底目录监听覆盖不到的场景。"
    ),
)
async def reconcile_libraries() -> None:
    db = get_database()
    async with db.session() as session:
        libraries = list((await session.execute(select(Library))).scalars().all())
    for library in libraries:
        assert library.id is not None
        summary = await scan_library(library.id)
        if summary.errors:
            logger.warning(
                "媒体库「%s」对账补扫存在问题：%s", library.name, "；".join(summary.errors)
            )
