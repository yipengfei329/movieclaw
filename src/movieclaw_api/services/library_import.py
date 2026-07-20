"""整理器（媒体库 L2 核心）：下载完成的种子 → 硬链入库 + 台账落账。

流程（docs/design/library.md M1）：
  种子文件清单 → 过滤视频文件 → 分配到集（文件名解析，enrich 复用）
  → 硬链到 ``{库主根}/{标题 (年份)}[/Season NN]/{规范文件名}``
  → ffprobe 探测 → ``library_file`` 落账（带来源种子）

关键决策：
- **硬链 + 规范命名**：下载区原文件原名继续做种（PT 保种刚需），库区文件
  改成 ``标题 (年份) - SxxEyy.ext`` 规范名——硬链改名零成本，且让
  Emby/Plex 在无 NFO 时也零歧义识别；
- 订阅入库的身份在投递时已锚定，这里只做"文件→集"的内层分配；
- 失败抛 ``LibraryImportError``（中文原因），由轮询任务记活动 + 退避重试，
  **文件滞留下载区绝不误删**；
- 跨文件系统（EXDEV）与路径不可达（容器映射）给出明确的部署引导。
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.services.library_config import (
    LibraryConfigService,
    derive_save_path,
    sanitize_folder_name,
)
from movieclaw_api.services.media_probe import probe_media
from movieclaw_db.models import FileSource, LibraryFile, MediaItem, Subscription, WantedItem
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository
from movieclaw_downloader.models import TorrentFile, TorrentStatus
from movieclaw_enrich import enrich
from movieclaw_media.models import MediaKind

logger = logging.getLogger("movieclaw_api.library_import")

# 视频文件扩展名（入库对象）；其余（字幕/nfo/图片）v1 不搬运
VIDEO_EXTS = {
    ".mkv",
    ".mp4",
    ".avi",
    ".ts",
    ".m2ts",
    ".wmv",
    ".mov",
    ".flv",
    ".rmvb",
    ".mpg",
    ".mpeg",
    ".m4v",
    ".webm",
}
# 文件名/路径含这些标记的视频不入库（样品片段等）
_IGNORE_MARKERS = ("sample",)


class LibraryImportError(Exception):
    """整理失败。message 是完整中文句子，直接进活动时间线。"""


@dataclass
class ImportOutcome:
    """一次整理的结果。"""

    library_name: str
    # 成功入库的期望单元（电影 = {(0,0)}）
    imported_units: set[tuple[int, int]] = field(default_factory=set)
    # 入库后的目标文件路径（时间线展示用）
    target_paths: list[str] = field(default_factory=list)
    # 未能分配到集的文件（中文原因，进活动提醒但不算失败）
    skipped: list[str] = field(default_factory=list)


async def import_completed_torrent(
    session: AsyncSession,
    *,
    subscription: Subscription,
    item: MediaItem,
    wanted_rows: list[WantedItem],
    status: TorrentStatus,
) -> ImportOutcome:
    """把一个已完成种子的内容整理进订阅的目标库。

    全部硬链成功才返回；任何环境性失败（无库/路径不可达/跨盘）抛
    LibraryImportError，文件留在下载区等待重试。
    """
    library = await LibraryConfigService(session).resolve_for_subscription(
        subscription.library_id, subscription.kind
    )
    if library is None:
        raise LibraryImportError(
            "未配置媒体库，无法整理入库；请在「媒体库」页为该类型创建一个库后自动重试"
        )
    dest_dir = derive_save_path(library, title=item.title, year=item.year)
    if dest_dir is None:
        raise LibraryImportError(f"媒体库「{library.name}」没有配置根路径，无法整理入库")

    source_root = _map_download_path(status.save_path)
    if not source_root.exists():
        raise LibraryImportError(
            f"下载器报告的保存目录在 movieclaw 中不可达：{status.save_path}"
            + (f"（映射后 {source_root}）" if str(source_root) != status.save_path else "")
            + "。如果 movieclaw 与下载器部署在不同容器/机器，请以相同路径挂载"
            "下载目录，或用环境变量 DOWNLOAD_PATH_MAPPING 配置路径映射"
            "（格式：下载器路径=>本地路径，分号分隔多组）"
        )

    videos = _video_files(status.files)
    if not videos:
        raise LibraryImportError(f"种子「{status.name}」中没有找到可入库的视频文件")

    # 种子名整体解析一次：片源/发布组等发布信息以种子名为准（比文件名更完整）
    release_attrs = enrich(status.name)

    kind = MediaKind(subscription.kind)
    plan = (
        _plan_movie(videos, item, dest_dir)
        if kind is MediaKind.MOVIE
        else _plan_tv(videos, item, wanted_rows, dest_dir)
    )
    if not plan.links:
        raise LibraryImportError(
            f"种子「{status.name}」的视频文件都无法解析出集号，未能入库；"
            "可在下载完成目录手动整理" + (f"（{plan.skipped[0]}）" if plan.skipped else "")
        )

    # 硬链（放线程池：os.link 与目录创建是阻塞 IO）
    await asyncio.to_thread(_link_all, source_root, plan)

    # NFO 写出（L4）：给条目目录留一份身份档案，Emby 零歧义、自家重扫免收敛。
    # 已存在的 NFO 不覆盖；失败只告警不阻断。
    from movieclaw_api.services.library_nfo import write_entry_nfo

    await asyncio.to_thread(write_entry_nfo, Path(dest_dir), item)

    # 探测 + 落账
    repo = LibraryFileRepository(session)
    outcome = ImportOutcome(library_name=library.name, skipped=plan.skipped)
    assert library.id is not None
    for link in plan.links:
        spec = await asyncio.to_thread(probe_media, link.target)
        target = Path(link.target)
        await repo.upsert_by_path(
            LibraryFile(
                library_id=library.id,
                media_item_id=item.id,
                season_number=link.season,
                episode_number=link.episode,
                file_path=str(target),
                size_bytes=link.size_bytes,
                container=target.suffix.lstrip(".").lower() or None,
                resolution=spec.resolution if spec else None,
                video_codec=spec.video_codec if spec else None,
                hdr=spec.hdr if spec else None,
                bit_depth=spec.bit_depth if spec else None,
                duration_seconds=spec.duration_seconds if spec else None,
                bit_rate=spec.bit_rate if spec else None,
                media_source=release_attrs.media_source,
                release_group=release_attrs.release_group,
                source=FileSource.IMPORTED,
                site_id=_payload_site(wanted_rows),
                torrent_id=None,
            )
        )
        outcome.imported_units.update(link.covered_units)
        outcome.target_paths.append(str(target))
    logger.info(
        "已入库《%s》到「%s」：%d 个文件 → %s",
        item.title,
        library.name,
        len(plan.links),
        dest_dir,
    )
    return outcome


# ---------------------------------------------------------------------------
# 计划：把视频文件映射到目标路径与期望单元
# ---------------------------------------------------------------------------


@dataclass
class _Link:
    source_rel: str  # 种子内相对路径
    target: str  # 目标绝对路径
    size_bytes: int
    season: int
    episode: int
    # 该文件覆盖的期望单元（多集合一文件时多于一个）
    covered_units: set[tuple[int, int]] = field(default_factory=set)


@dataclass
class _Plan:
    links: list[_Link] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _video_files(files: list[TorrentFile]) -> list[TorrentFile]:
    result = []
    for f in files:
        path = f.path.lower()
        if Path(path).suffix not in VIDEO_EXTS:
            continue
        if any(marker in path for marker in _IGNORE_MARKERS):
            continue
        result.append(f)
    return result


def _plan_movie(videos: list[TorrentFile], item: MediaItem, dest_dir: str) -> _Plan:
    """电影：取最大的视频文件为正片，规范名 ``标题 (年份).ext``。"""
    main = max(videos, key=lambda f: f.size_bytes)
    base = _entry_base_name(item)
    ext = Path(main.path).suffix.lower()
    return _Plan(
        links=[
            _Link(
                source_rel=main.path,
                target=str(Path(dest_dir) / f"{base}{ext}"),
                size_bytes=main.size_bytes,
                season=0,
                episode=0,
                covered_units={(0, 0)},
            )
        ]
    )


def _plan_tv(
    videos: list[TorrentFile],
    item: MediaItem,
    wanted_rows: list[WantedItem],
    dest_dir: str,
) -> _Plan:
    """剧集：逐文件解析季集号，规范名 ``标题 (年份) - SxxEyy.ext``。

    季号缺失时的兜底：本次工单只涉及一个季 → 用该季（整季包内文件常只写
    集号）。多集合一的文件（E01E02）落一行台账（首集），覆盖全部集单元。
    """
    plan = _Plan()
    base = _entry_base_name(item)
    wanted_seasons = {w.season_number for w in wanted_rows}
    fallback_season = wanted_seasons.pop() if len(wanted_seasons) == 1 else None

    for f in videos:
        stem = Path(f.path).name
        attrs = enrich(stem)
        episodes = sorted(set(attrs.episodes))
        season = (
            attrs.seasons[0] if len(set(attrs.seasons)) == 1 and attrs.seasons else fallback_season
        )
        if not episodes or season is None:
            plan.skipped.append(f"「{stem}」解析不出季集号")
            continue
        first = episodes[0]
        ext = Path(f.path).suffix.lower()
        name = f"{base} - S{season:02d}E{first:02d}{ext}"
        plan.links.append(
            _Link(
                source_rel=f.path,
                target=str(Path(dest_dir) / f"Season {season:02d}" / name),
                size_bytes=f.size_bytes,
                season=season,
                episode=first,
                covered_units={(season, e) for e in episodes},
            )
        )
    return plan


def _entry_base_name(item: MediaItem) -> str:
    """条目级规范名：``标题 (年份)``（中文优先，与库目录名一致）。"""
    base = sanitize_folder_name(item.title)
    return f"{base} ({item.year})" if item.year is not None else base


def _payload_site(wanted_rows: list[WantedItem]) -> str | None:
    """来源站点追溯暂缺工单直连字段，v1 留空；活动 payload 已有完整来源。"""
    return None


def _map_download_path(save_path: str) -> Path:
    """应用 DOWNLOAD_PATH_MAPPING：把下载器视角的路径换算成本进程可达路径。

    最长前缀优先；无匹配返回原路径。格式错误的条目跳过并告警一次。
    """
    from movieclaw_api.core.config import get_settings

    raw = get_settings().download_path_mapping.strip()
    if not raw:
        return Path(save_path)
    pairs: list[tuple[str, str]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        remote, sep, local = chunk.partition("=>")
        if not sep or not remote.strip() or not local.strip():
            logger.warning("DOWNLOAD_PATH_MAPPING 条目格式错误（应为 A=>B）：%s", chunk)
            continue
        pairs.append((remote.strip().rstrip("/"), local.strip().rstrip("/")))
    # 最长前缀优先，避免 "/data" 抢了 "/data/downloads" 的匹配
    for remote, local in sorted(pairs, key=lambda p: len(p[0]), reverse=True):
        if save_path == remote or save_path.startswith(remote + "/"):
            return Path(local + save_path[len(remote) :])
    return Path(save_path)


# ---------------------------------------------------------------------------
# 硬链执行（线程池内运行）
# ---------------------------------------------------------------------------


def _link_all(source_root: Path, plan: _Plan) -> None:
    for link in plan.links:
        src = source_root / link.source_rel
        dst = Path(link.target)
        if not src.exists():
            raise LibraryImportError(
                f"下载完成的文件在 movieclaw 中不可达：{src}。"
                "如果 movieclaw 与下载器在不同容器/机器，请以相同路径挂载下载目录"
            )
        if dst.exists():
            continue  # 幂等重入：目标已在（上次部分成功）
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(src, dst)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise LibraryImportError(
                    f"库目录与下载目录不在同一文件系统，无法硬链接入库"
                    f"（{src} → {dst}）。请把库主根与下载目录放在同一文件系统/存储卷，"
                    "复制模式在后续版本提供"
                ) from exc
            raise LibraryImportError(f"硬链接失败（{exc.strerror}）：{src} → {dst}") from exc
