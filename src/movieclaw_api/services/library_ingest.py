"""下载监听导入：监听目录里下载完成的内容 → 识别 → 硬链/复制进库主根。

与既有两条入库路径的关系（docs/design/library.md 语境）：
- 订阅管线靠**下载器 API 轮询**确认完成后硬链入库（download_progress）；
- 手动推送直接把 save_path 指到库内条目目录，靠库根监听发现；
- 本模块补第三条：**任何来源**落进监听导入源目录的文件（用户直接在
  qB 里加的种、网盘/浏览器下载等），完成后自动按规范命名搬进库。
  配置是独立的「监听导入规则」（import_watch 表：源目录 → 目标库 +
  策略），媒体库本身只有一套目录体系、不承载下载语义。

完成检测（本功能的核心难点——下载开始时目录结构就已建立）：
0. **下载器权威信号优先**：条目名能匹配到已配置下载器中的种子
   （比对种子名与落盘根名——**按名称匹配免疫容器路径映射**，save_path
   两侧视角不同不可靠）时，以下载器报告的完成状态为准：未完成就等，
   完成即处理、无需静默窗口——这是暂停种子误判的根治手段；
   匹配不到（网盘/浏览器等非种子来源）或下载器不可达时退回启发式：
1. **进行中标记排除**：条目树内存在下载器的未完成标记文件
   （qBittorrent ``.!qB`` / aria2 ``.aria2`` / 浏览器 ``.crdownload`` 等）
   即视为下载中，并重置静默计时（标记与权威信号矛盾时从严，按下载中处理）；
2. **静默窗口**：条目全树指纹（总大小:文件数:最大 mtime）连续稳定
   ``QUIET_SECONDS`` 才算落定。**不能只看大小**——BT 客户端普遍预分配
   全尺寸文件，大小从一开始就不变，mtime 才是写入活动的真实信号；
3. **ffprobe 终检**：入库前视频文件逐个探测，失败的不入库，挡住"暂停的
   种子恰好静默够久"的残缺文件（moov 在尾部的 mp4 未下完必然探测失败）；
   ffprobe 未安装时此门禁自动放行（与扫描器的降级行为一致）；
4. **指纹变化自动重试**：结论落 ``ingest_entry`` 台账，指纹变化（下载
   还在继续/季包补了新集）自动重新处理，失败条目另有小时级退避——
   这同时给了"边下边补集"的增量导入能力。

触发机制（事件驱动，尽量不主动扫——监听目录里的保种源会永远留着，
主动全量 stat 会让 NAS 磁盘永远无法休眠）：
- 正常路径：``IngestWatcher`` 对监听目录挂 watchdog，有事件才去抖巡检
  对应目录；下载写入持续产生事件，事件停止本身就是静默的开端；
- 静默到点自检：事件停了之后没人会再叫醒我们，每轮巡检后若仍有条目在
  等静默窗口，按最近到期时间挂一次性自检，全部落定即归零；
- 唯一的主动扫：目录**初次纳入监听**时补扫该目录一次——监听建立之前
  完成的下载（停机期间/目录刚就绪/刚加进配置）不会再产生事件，只有
  这一次能接住；之后该目录全靠事件驱动；
- 兜底：每小时重建一次失效监听，并只巡检**监听覆盖不到**的目录
  （目录不存在/watchdog 缺失/挂载不产生 fs 事件）；正被实时监听的目录
  绝不重复主动扫。

幂等与安全：
- 源文件**永不改动**：硬链保种零占用（需与主根同盘），复制适合跨盘；
- 搬运用 ``os.link`` 原子防覆盖落位（同 library_organize），目标已存在
  且内容不同时按多版本约定加 " - 版本标签" 后缀，仍冲突则跳过不覆盖；
- 台账逐条目收口：源文件留在监听目录，没有台账每轮都会重复处理。

与扫描/整理的并发：本模块只**新建**规范命名文件（与订阅入库管线同性质），
写入触发的库根 watchdog 扫描对已落账路径秒过；与整理的目标冲突由双方的
防覆盖改名兜底——均无需加锁（评估结论同 library_organize 模块头）。
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from movieclaw_api.services.library_config import derive_save_path
from movieclaw_api.services.library_import import (
    IN_PROGRESS_MARKERS,
    VIDEO_EXTS,
    _entry_base_name,
)
from movieclaw_api.services.library_resolve import verify_resolve
from movieclaw_api.services.library_scan import _guess_evidence, _season_from_dir
from movieclaw_api.services.media_discover import get_tmdb_client
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.media_probe import probe_media
from movieclaw_db.engine import get_database
from movieclaw_db.models import (
    FileSource,
    ImportWatch,
    IngestEntry,
    IngestStatus,
    Library,
    LibraryFile,
    MediaItem,
    utcnow,
)
from movieclaw_db.models.scheduled_task import TriggerType
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository
from movieclaw_enrich import enrich
from movieclaw_media.models import MediaKind
from movieclaw_scheduler.registry import register_task

logger = logging.getLogger("movieclaw_api.library_ingest")

# 兜底巡检节奏：正常路径是 watchdog 事件驱动，这里的低频全量巡检只兜底
# 网络挂载不产生 fs 事件、进程停机期间错过事件两种场景
FALLBACK_SWEEP_SECONDS = 3600
# 静默窗口：指纹连续稳定 5 分钟才认为下载落定（写入中 mtime 持续变化）
QUIET_SECONDS = 300
# 失败条目的重试退避：指纹没变化时每小时才重试一次（避免反复打 TMDB）
FAILED_RETRY_SECONDS = 3600
# 下载器种子概览的缓存：一轮事件风暴中的多个目录巡检共享一次 API 调用
_BRIEFS_TTL_SECONDS = 15.0

# 文件名含这些标记的视频不入库（与入库管线同口径）
_IGNORE_MARKERS = ("sample",)

# 进程内的静默观察：条目路径 -> (指纹, 首次见到该指纹的单调时钟)。
# 重启丢失只是重新等一个静默窗口，无需持久化
_stability: dict[str, tuple[str, float]] = {}
# 巡检串行锁：事件驱动与兜底巡检可能同时到达同一目录，串行化防同条目双处理
_sweep_lock = asyncio.Lock()
# 下载器种子概览缓存：(取样时刻, 概览列表或 None=不可用)
_briefs_cache: tuple[float, list | None] = (float("-inf"), None)


class IngestError(Exception):
    """单个文件搬运失败。message 是完整中文句子，直接进台账 message。"""


@dataclass
class _EntrySnapshot:
    """条目的一次快照：指纹 + 完成检测所需的观察结果。"""

    fingerprint: str  # 总大小:文件数:最大mtime
    has_marker: bool  # 树内存在下载中标记文件
    has_disc: bool  # 树内存在原盘结构（BDMV/VIDEO_TS）
    videos: list[Path] = field(default_factory=list)  # 可入库的视频文件


def _snapshot(entry: Path) -> _EntrySnapshot:
    """遍历条目（文件或目录）产出快照。纯 stat，线程池内运行。"""
    total, count, max_mtime = 0, 0, 0.0
    has_marker = False
    has_disc = False
    videos: list[Path] = []

    def visit(file: Path) -> None:
        nonlocal total, count, max_mtime, has_marker
        try:
            stat = file.stat()
        except OSError:
            return
        total += stat.st_size
        count += 1
        max_mtime = max(max_mtime, stat.st_mtime)
        lower = file.name.lower()
        if any(lower.endswith(marker) for marker in IN_PROGRESS_MARKERS):
            has_marker = True
            return
        if Path(lower).suffix in VIDEO_EXTS and not any(m in lower for m in _IGNORE_MARKERS):
            videos.append(file)

    if entry.is_file():
        visit(entry)
    else:
        stack = [entry]
        while stack:
            current = stack.pop()
            if current.name.upper() in ("BDMV", "VIDEO_TS"):
                has_disc = True
                continue
            try:
                children = sorted(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    if not child.name.startswith("."):
                        stack.append(child)
                elif child.is_file():
                    visit(child)
    return _EntrySnapshot(
        fingerprint=f"{total}:{count}:{int(max_mtime)}",
        has_marker=has_marker,
        has_disc=has_disc,
        videos=sorted(videos),
    )


# ---------------------------------------------------------------------------
# 巡检任务
# ---------------------------------------------------------------------------


async def _load_rules() -> list[tuple[ImportWatch, Library]]:
    """全部监听导入规则及其目标库（目标库已被删除的规则由外键级联清掉）。"""
    db = get_database()
    async with db.session() as session:
        result = await session.execute(
            select(ImportWatch, Library)
            .join(Library, ImportWatch.library_id == Library.id)  # type: ignore[arg-type]
            .order_by(ImportWatch.id)
        )
        return [(rule, library) for rule, library in result.all()]


@register_task(
    "library_ingest",
    title="监听导入（兜底巡检）",
    trigger_type=TriggerType.INTERVAL,
    interval_seconds=FALLBACK_SWEEP_SECONDS,
    description=(
        "低频兜底：重建失效的目录监听，并巡检监听覆盖不到的源目录"
        "（正在被实时监听的目录由事件驱动，不主动扫）。"
    ),
)
async def ingest_tick() -> None:
    # 先重建监听：目录此前未就绪（挂载晚于启动）或监听失效时借此恢复；
    # 新纳入监听的目录由 refresh_watches 触发一次该目录的补扫
    watcher = get_ingest_watcher()
    if watcher is not None:
        await watcher.refresh_watches()
    watched = watcher.watched_keys() if watcher is not None else frozenset()

    for rule, library in await _load_rules():
        if rule.source_path in watched:
            continue  # watchdog 在实时盯着：事件路径负责，不重复主动扫
        try:
            await _sweep_dir(library, rule.source_path, rule.strategy)
        except Exception:  # noqa: BLE001 -- 单目录失败不拖垮整轮
            logger.exception("监听导入巡检失败（→「%s」）：%s", library.name, rule.source_path)


async def _sweep_dir(library: Library, watch_root: str, strategy: str) -> None:
    """巡检一个监听目录：顶层每个文件/目录是一个条目（一次下载的产物）。"""
    root = Path(watch_root)
    if not root.is_dir():
        return  # 目录未就绪（挂载中/配置超前）：不告警刷屏，下轮再看
    async with _sweep_lock:
        briefs = await _downloader_briefs()
        try:
            entries = sorted(e for e in root.iterdir() if not e.name.startswith("."))
        except OSError as exc:
            logger.warning("读取监听目录失败（%s）：%s", watch_root, exc)
            return
        seen = {str(e) for e in entries}
        for entry in entries:
            try:
                await _process_entry(library, root, entry, strategy, briefs)
            except Exception:  # noqa: BLE001 -- 单条目失败不断整轮
                logger.exception("处理监听条目失败：%s", entry)
        # 条目从监听目录消失（用户删源）后清掉它的静默观察，防字典无界增长
        prefix = str(root).rstrip("/") + "/"
        for key in [k for k in _stability if k.startswith(prefix) and k not in seen]:
            _stability.pop(key, None)


async def _downloader_briefs() -> list | None:
    """全部可用下载器的种子概览（带短缓存）；无下载器/全部不可达返回 None。

    None 表示"权威信号缺席"——调用方退回启发式检测，而不是把所有条目
    误判成"未在下载"。
    """
    global _briefs_cache
    now = time.monotonic()
    cached_at, cached = _briefs_cache
    if now - cached_at < _BRIEFS_TTL_SECONDS:
        return cached
    # 局部导入：复用订阅管线的"可用下载器"口径，避免模块加载期的重依赖
    from movieclaw_api.services.download_progress import _usable_downloaders
    from movieclaw_downloader import create_downloader

    db = get_database()
    async with db.session() as session:
        downloaders = await _usable_downloaders(session)
    briefs: list = []
    any_ok = False
    for row, config in downloaders:
        adapter = create_downloader(config)
        try:
            briefs.extend(await adapter.list_torrents())
            any_ok = True
        except Exception as exc:  # noqa: BLE001 -- 单台不可达降级继续
            logger.warning("列出下载器「%s」的种子失败：%s", row.name, exc)
        finally:
            await adapter.close()
    result = briefs if any_ok else None
    _briefs_cache = (now, result)
    return result


def _torrent_verdict(entry_name: str, briefs: list | None) -> str | None:
    """按名称把条目匹配到下载器种子："complete" / "downloading" / None（未匹配）。

    条目名就是种子的落盘根目录/文件名，比对种子名与 content_name 两个口径
    ——名称匹配免疫容器路径映射。同名多个种子从严：任一未完成即视为下载中。
    """
    matches = [b for b in briefs or [] if entry_name in (b.name, b.content_name)]
    if not matches:
        return None
    return "complete" if all(b.completed for b in matches) else "downloading"


async def _process_entry(
    library: Library, watch_root: Path, entry: Path, strategy: str, briefs: list | None
) -> None:
    path_str = str(entry)
    snap = await asyncio.to_thread(_snapshot, entry)
    # 权威信号优先：能匹配到下载器种子时以下载器状态为准；标记文件与权威
    # 信号矛盾（说完成却还有 .!qB 等）说明匹配可疑，从严按下载中处理
    verdict = _torrent_verdict(entry.name, briefs)
    if snap.has_marker or verdict == "downloading":
        _stability.pop(path_str, None)
        return

    db = get_database()
    async with db.session() as session:
        record = (
            await session.execute(select(IngestEntry).where(IngestEntry.entry_path == path_str))
        ).scalar_one_or_none()
        if record is not None and record.fingerprint == snap.fingerprint:
            if record.status != IngestStatus.FAILED:
                return  # 已处理且没变化
            if (utcnow() - record.attempted_at).total_seconds() < FAILED_RETRY_SECONDS:
                return  # 失败退避中

        if verdict == "complete":
            # 下载器确认完成：跳过静默窗口，立即处理
            _stability.pop(path_str, None)
        else:
            # 启发式兜底（非种子来源/下载器不可用）：
            # 同一指纹连续稳定 QUIET_SECONDS 才认为下载落定
            now = time.monotonic()
            previous = _stability.get(path_str)
            if previous is None or previous[0] != snap.fingerprint:
                _stability[path_str] = (snap.fingerprint, now)
                return
            if now - previous[1] < QUIET_SECONDS:
                return

        await _ingest_entry(session, library, watch_root, entry, strategy, snap, record)


# ---------------------------------------------------------------------------
# 单条目入库
# ---------------------------------------------------------------------------


def _ffprobe_available() -> bool:
    """ffprobe 是否可用——不可用时探测门禁放行（与扫描器降级行为一致）。"""
    return shutil.which("ffprobe") is not None


async def _ingest_entry(
    session,
    library: Library,
    watch_root: Path,
    entry: Path,
    strategy: str,
    snap: _EntrySnapshot,
    record: IngestEntry | None,
) -> None:
    async def conclude(status: IngestStatus, message: str, imported: int = 0) -> None:
        await _save_record(
            session, library, str(entry), snap.fingerprint, record, status, message, imported
        )

    if snap.has_disc:
        await conclude(
            IngestStatus.SKIPPED, "原盘目录（BDMV/VIDEO_TS）暂不支持自动入库，请手动整理"
        )
        return
    if not snap.videos:
        await conclude(IngestStatus.SKIPPED, "条目中没有视频文件，已跳过")
        return

    kind = MediaKind(library.kind)
    main = max(snap.videos, key=lambda f: f.stat().st_size)
    spec = await asyncio.to_thread(probe_media, main)
    if spec is None and _ffprobe_available():
        await conclude(
            IngestStatus.FAILED,
            f"主视频「{main.name}」探测失败——可能尚未下载完成或已损坏，文件变化后自动重试",
        )
        return

    item = await _identify(session, kind, watch_root, main, spec)
    if item is None:
        await conclude(
            IngestStatus.FAILED,
            f"无法识别「{entry.name}」对应的影视条目；"
            "可把条目改名为「标题 (年份)」形式后自动重试，或手动整理",
        )
        return

    dest_dir = derive_save_path(library, title=item.title, year=item.year)
    if dest_dir is None:
        await conclude(IngestStatus.FAILED, f"媒体库「{library.name}」没有配置根路径，无法入库")
        return

    # 发布信息以条目名为准（比单集文件名完整），与入库管线的种子名口径一致
    release_attrs = enrich(entry.name if entry.is_dir() else entry.stem)
    base = _entry_base_name(item)
    repo = LibraryFileRepository(session)
    assert library.id is not None and item.id is not None

    files = [main] if kind is MediaKind.MOVIE else list(snap.videos)
    notes: list[str] = []
    if kind is MediaKind.MOVIE and len(snap.videos) > 1:
        notes.append(f"已取最大文件为正片，忽略其余 {len(snap.videos) - 1} 个视频")

    imported = 0
    for file in files:
        if kind is MediaKind.MOVIE:
            season, episode = 0, 0
        else:
            season, episode = _unit(file, entry)
            if not episode:
                notes.append(f"「{file.name}」解析不出集号，未入库")
                continue
            if season is None:
                notes.append(f"「{file.name}」解析不出季号，未入库")
                continue
        ext = file.suffix.lower()
        if kind is MediaKind.MOVIE:
            target = Path(dest_dir) / f"{base}{ext}"
        else:
            target = (
                Path(dest_dir)
                / f"Season {season:02d}"
                / f"{base} - S{season:02d}E{episode:02d}{ext}"
            )
        file_spec = spec if file == main else await asyncio.to_thread(probe_media, file)
        # 门禁逐文件生效：暂停的季包可能前几集完整、后几集残缺，主文件
        # 探测通过不代表每个文件都完整
        if file_spec is None and _ffprobe_available():
            notes.append(f"「{file.name}」探测失败（可能不完整），未入库；文件变化后自动重试")
            continue
        label = (file_spec.resolution if file_spec else None) or release_attrs.media_source or "V2"
        try:
            final = await asyncio.to_thread(_transfer, file, target, strategy, label)
        except IngestError as exc:
            notes.append(str(exc))
            continue
        if final is None:
            continue  # 同一内容已在库（重复处理/增量重扫），静默幂等
        await repo.upsert_by_path(
            LibraryFile(
                library_id=library.id,
                media_item_id=item.id,
                season_number=season,
                episode_number=episode,
                file_path=str(final),
                size_bytes=file.stat().st_size,
                container=final.suffix.lstrip(".").lower() or None,
                resolution=file_spec.resolution if file_spec else None,
                video_codec=file_spec.video_codec if file_spec else None,
                hdr=file_spec.hdr if file_spec else None,
                bit_depth=file_spec.bit_depth if file_spec else None,
                duration_seconds=file_spec.duration_seconds if file_spec else None,
                bit_rate=file_spec.bit_rate if file_spec else None,
                media_source=release_attrs.media_source,
                release_group=release_attrs.release_group,
                source=FileSource.IMPORTED,
            )
        )
        imported += 1

    if imported:
        # NFO 身份档案：Emby 零歧义、自家重扫免收敛（已存在不覆盖，失败不阻断）
        from movieclaw_api.services.library_nfo import write_entry_nfo

        await asyncio.to_thread(write_entry_nfo, Path(dest_dir), item)

    verb = "硬链接" if strategy == "hardlink" else "复制"
    if imported:
        message = f"已识别为《{item.title}》，{verb} {imported} 个文件到 {dest_dir}"
        if notes:
            message += "；" + "；".join(notes)
        await conclude(IngestStatus.IMPORTED, message, imported)
    elif notes:
        await conclude(IngestStatus.FAILED, "；".join(notes))
    else:
        # 全部文件都已在库（此前处理过/多下载器重复下载）：结论记 imported
        await conclude(IngestStatus.IMPORTED, f"《{item.title}》的内容已全部在库，无需搬运")


async def _identify(
    session, kind: MediaKind, watch_root: Path, main: Path, spec
) -> MediaItem | None:
    """识别条目身份：条目名/文件名解析 → TMDB 证据验证收敛（扫描器同链）。"""
    evidence = _guess_evidence(kind, watch_root, main)
    if evidence is None:
        return None
    evidence.duration_seconds = spec.duration_seconds if spec else None
    try:
        tmdb_id = await verify_resolve(get_tmdb_client(), kind, evidence)
        if tmdb_id is None:
            return None
        return await MediaLibraryService(session, get_tmdb_client()).ensure_media_item(
            kind, tmdb_id
        )
    except Exception as exc:  # noqa: BLE001 -- TMDB 波动不该让条目卡死在 failed
        logger.warning("TMDB 收敛失败（%s）：%s", main.name, exc)
        return None


def _unit(file: Path, entry: Path) -> tuple[int | None, int]:
    """剧集文件的季集号：文件名 → 父目录 Season 模式 → 条目名季号兜底。

    季号三处都拿不到时返回 None（宁可跳过，不默认第 1 季错挂）；
    显式 S00（特别篇）会被文件名解析正常带出，不走兜底。
    """
    attrs = enrich(file.stem)
    episode = attrs.episodes[0] if attrs.episodes else 0
    season: int | None = attrs.seasons[0] if attrs.seasons else _season_from_dir(file.parent)
    if season is None:
        entry_attrs = enrich(entry.name if entry.is_dir() else entry.stem)
        entry_seasons = {s for s in entry_attrs.seasons}
        season = entry_seasons.pop() if len(entry_seasons) == 1 else None
    return season, episode


# ---------------------------------------------------------------------------
# 搬运（线程池内运行）
# ---------------------------------------------------------------------------


def _same_payload(a: Path, b: Path) -> bool:
    """两个路径是否同一内容：同一 inode（硬链过）或尺寸相同（复制过）。"""
    try:
        if os.path.samefile(a, b):
            return True
        return a.stat().st_size == b.stat().st_size
    except OSError:
        return False


def _transfer(src: Path, dst: Path, strategy: str, version_label: str) -> Path | None:
    """把源文件按策略搬到目标；返回最终落位路径，None = 同内容已在库。

    目标已存在且内容不同时按多版本约定退让到 ``… - 版本标签.ext``；
    落位一律走 ``os.link`` 原子防覆盖（复制策略先写 .part 临时文件——
    库根的 watchdog/扫描不认 .part 后缀，不会看到半成品）。
    """
    final = dst
    if final.exists():
        if _same_payload(src, final):
            return None
        final = dst.with_name(f"{dst.stem} - {version_label}{dst.suffix}")
        if final.exists():
            if _same_payload(src, final):
                return None
            raise IngestError(f"目标已存在同名文件，跳过以免覆盖：{final.name}")
    try:
        final.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise IngestError(f"创建目标目录失败（{exc.strerror}）：{final.parent}") from exc

    if strategy == "copy":
        part = final.with_name(final.name + ".part")
        try:
            shutil.copyfile(src, part)
            os.link(part, final)
        except FileExistsError as exc:
            raise IngestError(f"目标已存在同名文件，跳过以免覆盖：{final.name}") from exc
        except OSError as exc:
            raise IngestError(f"复制失败（{exc.strerror}）：{src.name} → {final}") from exc
        finally:
            part.unlink(missing_ok=True)
        return final

    try:
        os.link(src, final)
    except FileExistsError as exc:
        raise IngestError(f"目标已存在同名文件，跳过以免覆盖：{final.name}") from exc
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise IngestError(
                f"硬链接失败：监听目录与库主根不在同一文件系统（{src.name}）。"
                "请把该监听目录的策略改为「复制」，或把两者放到同一存储卷"
            ) from exc
        raise IngestError(f"硬链接失败（{exc.strerror}）：{src.name} → {final}") from exc
    return final


# ---------------------------------------------------------------------------
# 台账
# ---------------------------------------------------------------------------


async def _save_record(
    session,
    library: Library,
    entry_path: str,
    fingerprint: str,
    record: IngestEntry | None,
    status: IngestStatus,
    message: str,
    imported: int,
) -> None:
    now = utcnow()
    if record is None:
        record = IngestEntry(
            library_id=library.id,  # type: ignore[arg-type]
            entry_path=entry_path,
            fingerprint=fingerprint,
            status=status,
            message=message,
            imported_count=imported,
            attempted_at=now,
        )
        session.add(record)
    else:
        record.fingerprint = fingerprint
        record.status = status
        record.message = message
        record.imported_count += imported
        record.attempted_at = now
        record.updated_at = now
    await session.commit()
    _stability.pop(entry_path, None)
    if status is IngestStatus.FAILED:
        logger.warning("监听导入未完成（%s）：%s", entry_path, message)
    else:
        logger.info("监听导入（%s）：%s", entry_path, message)


# ---------------------------------------------------------------------------
# 事件驱动：监听目录的 watchdog 观察者（进程级单例，模式同 library_watch）
# ---------------------------------------------------------------------------

# 事件去抖：首个事件后等安静 3 秒再巡检；持续有事件时最长 30 秒必巡检一次
_EVENT_QUIET_SECONDS = 3.0
_EVENT_MAX_WAIT_SECONDS = 30.0


class IngestWatcher:
    """下载监听目录的文件事件观察者。

    事件驱动是本功能的既定形态（不做常驻轮询）：下载写入持续产生 fs
    事件，事件停止即静默的开端；没有下载活动时零开销，NAS 磁盘可以休眠。
    静默窗口的"到点检查"没有事件会叫醒——每轮巡检后若仍有条目在等静默，
    按最近到期时间挂一个一次性自检任务，条目全部落定后自然归零。
    """

    def __init__(self) -> None:
        self._observer = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._consumer: asyncio.Task | None = None
        self._catchup: asyncio.Task | None = None
        self._available = True
        # 当前实际在监听的源目录集合：兜底巡检据此跳过它们（事件路径已负责）
        self._watched: set[str] = set()
        # 静默到点自检任务：每个源目录至多挂一个
        self._rechecks: dict[str, asyncio.Task] = {}

    def watched_keys(self) -> frozenset[str]:
        """实际在监听的源目录集合——兜底巡检只扫不在此列的目录。"""
        return frozenset(self._watched)

    # -- 生命周期 ----------------------------------------------------------

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._consumer = asyncio.create_task(self._consume())
        # refresh_watches 会把初次纳入监听的目录逐个投队列补扫（覆盖停机
        # 期间完成的下载），不做独立的全量扫描
        await self.refresh_watches()
        if not self._available:
            # watchdog 缺失：事件路径不存在，开机全量补扫一次顶上
            self._catchup = asyncio.create_task(ingest_tick())

    async def stop(self) -> None:
        for task in (self._consumer, self._catchup, *self._rechecks.values()):
            if task is not None:
                task.cancel()
        self._consumer = None
        self._catchup = None
        self._rechecks.clear()
        self._stop_observer()

    def _stop_observer(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None

    async def refresh_watches(self) -> None:
        """按当前监听导入规则重建监听（规则增删改后调用）。"""
        if not self._available:
            return
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            self._available = False
            logger.warning("未安装 watchdog，监听导入不实时——完成的下载靠每小时兜底巡检发现")
            return

        rules = await _load_rules()

        watcher = self

        class _Handler(FileSystemEventHandler):
            """事件回调（观察者线程）：只投递源目录标识，不做任何业务。"""

            def __init__(self, source_path: str) -> None:
                self._key = source_path

            def on_any_event(self, event) -> None:  # noqa: ANN001
                watcher._enqueue_threadsafe(self._key)

        self._stop_observer()
        observer = Observer()
        watched: set[str] = set()
        for rule, _library in rules:
            if not Path(rule.source_path).is_dir():
                continue  # 目录未就绪：兜底巡检持续兜着，不告警刷屏
            try:
                observer.schedule(_Handler(rule.source_path), rule.source_path, recursive=True)
                watched.add(rule.source_path)
            except OSError as exc:
                logger.warning("监听源目录失败（%s）：%s", rule.source_path, exc)
        # 初次纳入监听的目录补扫一次：监听建立之前完成的下载（停机期间/
        # 目录刚就绪/刚加进配置）不会再产生事件，只有这一次主动扫能接住；
        # 之后全靠事件驱动，不再主动扫它
        newly_watched = watched - self._watched
        self._watched = watched
        if watched:
            observer.daemon = True
            observer.start()
            self._observer = observer
            logger.info("监听导入已启动：监听 %d 个源目录", len(watched))
        for key in sorted(newly_watched):
            self._queue.put_nowait(key)

    # -- 事件通道 ----------------------------------------------------------

    def _enqueue_threadsafe(self, key: str) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._queue.put_nowait, key)

    async def _consume(self) -> None:
        """去抖消费：首事件后等安静窗口，汇总本批涉及的监听目录逐个巡检。"""
        while True:
            first = await self._queue.get()
            pending = {first}
            deadline = asyncio.get_running_loop().time() + _EVENT_MAX_WAIT_SECONDS
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                timeout = min(_EVENT_QUIET_SECONDS, max(remaining, 0))
                try:
                    pending.add(await asyncio.wait_for(self._queue.get(), timeout))
                except TimeoutError:
                    break  # 安静窗口达成
                if asyncio.get_running_loop().time() >= deadline:
                    break  # 兜底：持续有事件也要巡检
            for source_path in sorted(pending):
                try:
                    await self._sweep(source_path)
                except Exception:  # noqa: BLE001 -- 监听消费绝不崩
                    logger.exception("事件触发的监听目录巡检失败：%s", source_path)

    async def _sweep(self, source_path: str) -> None:
        db = get_database()
        async with db.session() as session:
            result = await session.execute(
                select(ImportWatch, Library)
                .join(Library, ImportWatch.library_id == Library.id)  # type: ignore[arg-type]
                .where(ImportWatch.source_path == source_path)
            )
            pair = result.first()
        if pair is None:
            return  # 规则已删除（refresh_watches 稍后会拆掉监听）
        rule, library = pair
        await _sweep_dir(library, source_path, rule.strategy)
        self._arm_recheck(source_path)

    def _arm_recheck(self, source_path: str) -> None:
        """仍有条目在等静默窗口时，按最近到期时间挂一次性自检。"""
        prefix = source_path.rstrip("/") + "/"
        pending = [since for path, (_fp, since) in _stability.items() if path.startswith(prefix)]
        if not pending:
            return
        existing = self._rechecks.get(source_path)
        if existing is not None and not existing.done():
            return
        delay = min(pending) + QUIET_SECONDS - time.monotonic() + 1.0
        delay = max(5.0, min(delay, QUIET_SECONDS + 5.0))
        self._rechecks[source_path] = asyncio.create_task(self._recheck_later(source_path, delay))

    async def _recheck_later(self, key: str, delay: float) -> None:
        await asyncio.sleep(delay)
        self._queue.put_nowait(key)


_watcher: IngestWatcher | None = None


def get_ingest_watcher() -> IngestWatcher | None:
    return _watcher


async def init_ingest_watcher() -> None:
    """启动进程级监听单例（lifespan 调用）。"""
    global _watcher
    _watcher = IngestWatcher()
    await _watcher.start()


async def close_ingest_watcher() -> None:
    global _watcher
    if _watcher is not None:
        await _watcher.stop()
        _watcher = None
