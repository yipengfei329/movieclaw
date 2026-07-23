"""整理器（存量规范化）：把库里已识别的文件按刮削结果批量重命名归位。

存量扫描（library_scan）只识别落账、绝不动磁盘，因此部署前就存在的文件
会一直保持原来杂乱的名字。本模块补上"让库变规整"的主动能力：

  台账在位文件 → 计算规范目标路径（与入库管线同一套命名模板）
    ``{所在根}/{标题 (年份)}[/Season NN]/{标题 (年份)[ - SxxEyy][ - 版本标签]}.ext``
  → 用户在前端预览确认 → 逐文件改名 + 台账路径随迁 → 清理搬空的目录

设计决策：
- **预览与执行分离**：``build_organize_plan`` 是纯计算（只读磁盘不写），
  预览接口直接返回完整清单；执行时**重新计算**计划——预览到确认之间
  磁盘可能已变化，执行永远以最新状态为准；
- **防覆盖改名**：同文件系统内用 ``os.link + os.unlink`` 代替 ``os.rename``
  ——link 遇到目标已存在会原子失败（EEXIST），不会像 rename 那样静默
  覆盖（与入库管线并发写入同一规范名时的兜底）；不支持硬链的文件系统
  退回"先查存在再 rename"；
- **逐文件收口**：每改名成功一个立即随迁台账（repo.relocate），中途失败
  不会留下"账实不符"的批量烂摊子，单文件失败记入 errors 不断整轮；
- **只清理自己搬空的目录**：改名后仅对被搬走文件的原目录（及其空祖先）
  尝试 rmdir——非空即停，绝不触碰与本次整理无关的目录，绝不删除文件；
- **多版本按播放器规范命名**：同条目多个版本（1080p 与 2160p 并存）落
  同一条目目录，文件名加 `` - 版本标签`` 后缀（如 ``标题 (年份) - 2160p.ext``）
  ——Emby / Plex / Jellyfin 都按此约定把它们归组为同一影片的不同版本。
  标签优先取分辨率，撞车时逐级追加片源/发布组，全部探测不到退回按文件
  大小编号（V1/V2…）；标签推导是确定性的，重跑整理不会来回改名。

与其他任务的互斥（仔细评估的结论）：
- **与扫描双向互斥**：扫描的改名归并（_try_relink）用"旧路径消失 + 新路径
  出现"做指纹匹配，与整理的批量改名并发会竞态——扫描可能把整理刚搬走的
  旧路径标 missing、把新路径当新文件重走识别链（人工认领可能丢失）。
  扫描的三个入口（手动路由 / watchdog 去抖 / 6 小时对账）都收敛到
  ``scan_library``，在那里统一用 ``is_organizing`` 挡下；整理开始前同样
  检查 ``is_scanning``。整理产生的 rename 事件会触发 watchdog 的去抖扫描
  并被该守卫挡下——台账在整理中已同步更新，无需扫描补账，漏掉的事件
  由 6 小时对账兜底。
- **与入库管线（下载完成硬链）不加锁**：入库只新建规范命名的文件，与
  整理的冲突面仅剩"同一单元恰好同名"——执行时目标已存在即跳过（防覆盖
  改名保证不清掉入库产物）；为此把下载轮询与整理跨任务加锁，复杂度
  不成比例。
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from sqlmodel import select

from movieclaw_api.services.library_config import sanitize_folder_name
from movieclaw_api.services.library_import import VIDEO_EXTS, _entry_base_name
from movieclaw_db.engine import get_database
from movieclaw_db.models import Library, LibraryFile, MediaItem, utcnow
from movieclaw_db.repositories.library_file_repo import LibraryFileRepository
from movieclaw_media.models import MediaKind

logger = logging.getLogger("movieclaw_api.library_organize")

# 跟随主文件一起改名的附属文件后缀（字幕/章节/单文件 NFO 等，
# 同目录且文件名以"主文件名."开头即视为附属，如 foo.zh.srt / foo.nfo）
_SIDECAR_SKIP_EXTS = VIDEO_EXTS | {".iso"}  # 同名不同容器的视频是独立版本，不是附属

# 同一时间每个库只允许一个整理在跑（进程内互斥，与 library_scan 同模式）
_organizing: set[int] = set()
# 整理进行中的实时进度 (已完成, 总数)：前端轮询后画进度
_progress: dict[int, tuple[int, int]] = {}
# 每库最近一次整理的结论（给前端"整理完成了什么"的反馈）
_last: dict[int, tuple] = {}


def is_organizing(library_id: int) -> bool:
    return library_id in _organizing


def organize_progress(library_id: int) -> tuple[int, int] | None:
    """进行中整理的 (已完成, 总数)；没有整理在跑则为 None。"""
    return _progress.get(library_id)


def last_organize(library_id: int) -> tuple | None:
    """最近一次整理的 (完成时间, OrganizeSummary)；从未整理过则为 None。"""
    return _last.get(library_id)


# ---------------------------------------------------------------------------
# 计划：纯计算，预览接口与执行共用
# ---------------------------------------------------------------------------


@dataclass
class SidecarMove:
    """跟随主文件改名的附属文件（字幕等）。"""

    source_path: str
    target_path: str


@dataclass
class RenameAction:
    """一个待改名文件的完整计划。"""

    file_id: int
    media_item_id: int
    title: str
    year: int | None
    source_path: str
    target_path: str
    # 相对所在根的路径（前端展示用，绝对路径太长）
    source_rel: str
    target_rel: str
    size_bytes: int
    # 版本标签素材（多版本同名时用来生成 " - 2160p" 这类后缀）
    resolution: str | None = None
    media_source: str | None = None
    release_group: str | None = None
    sidecars: list[SidecarMove] = field(default_factory=list)


@dataclass
class SkipEntry:
    """不参与整理的文件与中文原因（预览里逐条展示，用户心里有数）。"""

    file_path: str
    reason: str


@dataclass
class OrganizePlan:
    """一次整理的完整计划。"""

    library_id: int
    total: int = 0  # 台账在位文件总数（= 改名 + 已规范 + 跳过）
    already_ok: int = 0  # 已符合规范命名，无需动作
    renames: list[RenameAction] = field(default_factory=list)
    skips: list[SkipEntry] = field(default_factory=list)


async def build_organize_plan(session, library: Library) -> OrganizePlan:
    """计算整理计划：只读磁盘与台账，不做任何写入。

    目标路径在文件**当前所在的根**下生成（不跨根移动——扩展根常在
    另一块盘上，跨根改名等于跨盘复制，不是本功能的职责）。
    """
    assert library.id is not None
    result = await session.execute(
        select(LibraryFile, MediaItem)
        .join(MediaItem, LibraryFile.media_item_id == MediaItem.id, isouter=True)  # type: ignore[arg-type]
        .where(LibraryFile.library_id == library.id)
        .order_by(LibraryFile.file_path)
    )
    rows = [(f, item) for f, item in result.all()]
    kind = MediaKind(library.kind)
    roots = [r.rstrip("/") for r in library.root_paths]
    # 磁盘检查（exists/is_dir/附属文件枚举）放线程池：大库上千次 stat 不该阻塞事件循环
    return await asyncio.to_thread(_build_plan_sync, library.id, kind, roots, rows)


def _build_plan_sync(
    library_id: int,
    kind: MediaKind,
    roots: list[str],
    rows: list[tuple[LibraryFile, MediaItem | None]],
) -> OrganizePlan:
    plan = OrganizePlan(library_id=library_id)
    candidates: list[RenameAction] = []
    for row, item in rows:
        if row.missing_since is not None:
            continue  # 缺失文件不计入总数也不展示——它不在磁盘上，无从整理
        plan.total += 1
        src = Path(row.file_path)
        if item is None:
            plan.skips.append(SkipEntry(row.file_path, "尚未识别身份，请先在「待识别」里认领"))
            continue
        root = _root_of(roots, row.file_path)
        if root is None:
            plan.skips.append(
                SkipEntry(row.file_path, "不在库的任何根路径下（根路径可能已变更），请重新扫描")
            )
            continue
        if src.is_dir():
            plan.skips.append(SkipEntry(row.file_path, "原盘目录（BDMV/VIDEO_TS）整体保持原结构"))
            continue
        if kind is MediaKind.TV and row.episode_number == 0:
            plan.skips.append(
                SkipEntry(row.file_path, "解析不出集号，无法生成规范文件名（可在待识别里修正季集）")
            )
            continue
        base = _entry_base_name(item)
        ext = src.suffix.lower()
        if kind is MediaKind.MOVIE:
            target = Path(root) / base / f"{base}{ext}"
        else:
            season = row.season_number
            target = (
                Path(root)
                / base
                / f"Season {season:02d}"
                / f"{base} - S{season:02d}E{row.episode_number:02d}{ext}"
            )
        if str(target) == row.file_path:
            plan.already_ok += 1
            continue
        assert row.id is not None and item.id is not None
        candidates.append(
            RenameAction(
                file_id=row.id,
                media_item_id=item.id,
                title=item.title,
                year=item.year,
                source_path=row.file_path,
                target_path=str(target),
                source_rel=row.file_path[len(root) + 1 :],
                target_rel=str(target)[len(root) + 1 :],
                size_bytes=row.size_bytes,
                resolution=row.resolution,
                media_source=row.media_source,
                release_group=row.release_group,
            )
        )

    # 同名处理：多个文件算出同一规范名 = 同条目多版本，按 Emby/Plex 的
    # 多版本约定加 " - 版本标签" 后缀落同一目录归组；规范名被同条目的
    # 在位文件占用时，本文件同样作为附加版本加标签。加标签后仍撞车、
    # 或目标被无关文件占用则跳过——宁可留乱，绝不覆盖
    in_place_item: dict[str, int] = {
        row.file_path: item.id
        for row, item in rows
        if item is not None and item.id is not None and row.missing_since is None
    }
    by_target: dict[str, list[RenameAction]] = {}
    for action in candidates:
        by_target.setdefault(action.target_path, []).append(action)
    taken: set[str] = set()
    for target, actions in by_target.items():
        if len(actions) > 1:
            for action, label in zip(actions, _version_labels(actions), strict=True):
                _apply_version_label(action, label)
        elif in_place_item.get(target) == actions[0].media_item_id:
            # 规范名的占用者是同条目的在位文件（already_ok 的那份保持无标签，
            # Emby/Plex 按相同基础名照样归组）→ 本文件作为附加版本
            _apply_version_label(actions[0], _attr_label(actions[0]) or "V2")
        for action in actions:
            if action.target_path == action.source_path:
                plan.already_ok += 1  # 上一轮整理已加过标签的版本文件（幂等）
                continue
            if action.target_path in taken or Path(action.target_path).exists():
                plan.skips.append(
                    SkipEntry(action.source_path, "目标路径已存在同名文件，跳过以免覆盖")
                )
                continue
            taken.add(action.target_path)
            action.sidecars = _find_sidecars(action)
            plan.renames.append(action)
    plan.renames.sort(key=lambda a: a.target_path)
    return plan


def _version_labels(actions: list[RenameAction]) -> list[str]:
    """为同名多版本生成互不相同的版本标签。

    逐级增加信息量直到组内唯一：分辨率 → +片源 → +发布组；三级都无法
    区分（探测/解析信息缺失）退回按文件大小从大到小编号（V1/V2…）。
    每一级都是确定性的，重跑整理时标签不变、不会来回改名。
    """
    picks = (
        lambda a: [a.resolution],
        lambda a: [a.resolution, a.media_source],
        lambda a: [a.resolution, a.media_source, a.release_group],
    )
    for pick in picks:
        raw = [" ".join(p for p in pick(a) if p) for a in actions]
        if all(raw) and len(set(raw)) == len(raw):
            return [sanitize_folder_name(label) for label in raw]
    order = sorted(
        range(len(actions)), key=lambda i: (-actions[i].size_bytes, actions[i].source_path)
    )
    labels = [""] * len(actions)
    for rank, index in enumerate(order, start=1):
        labels[index] = f"V{rank}"
    return labels


def _attr_label(action: RenameAction) -> str | None:
    """单个附加版本的标签：分辨率优先，缺失退片源/发布组；全缺返回 None。"""
    raw = action.resolution or action.media_source or action.release_group
    return sanitize_folder_name(raw) if raw else None


def _apply_version_label(action: RenameAction, label: str) -> None:
    """把版本标签织入目标文件名：``…/标题 (年份)[ - SxxEyy] - 标签.ext``。"""
    dst = Path(action.target_path)
    named = dst.with_name(f"{dst.stem} - {label}{dst.suffix}")
    root_len = len(action.target_path) - len(action.target_rel)
    action.target_path = str(named)
    action.target_rel = str(named)[root_len:]


def _root_of(roots: list[str], file_path: str) -> str | None:
    """文件所在的库根（最长前缀优先）；不在任何根下返回 None。"""
    best = None
    for root in roots:
        if file_path.startswith(root + "/") and (best is None or len(root) > len(best)):
            best = root
    return best


def _find_sidecars(action: RenameAction) -> list[SidecarMove]:
    """主文件的附属文件：同目录、文件名以"主文件名."开头（如 foo.zh.srt）。

    同名不同容器的视频（foo.mkv 旁的 foo.mp4）是独立版本不是附属，排除。
    """
    src = Path(action.source_path)
    dst = Path(action.target_path)
    moves = []
    try:
        entries = list(src.parent.iterdir())
    except OSError:
        return []
    prefix = src.stem + "."
    for entry in sorted(entries):
        if not entry.is_file() or entry == src or not entry.name.startswith(prefix):
            continue
        if entry.suffix.lower() in _SIDECAR_SKIP_EXTS:
            continue
        tail = entry.name[len(src.stem) :]  # 含开头的 "."，如 ".zh.srt"
        moves.append(SidecarMove(str(entry), str(dst.parent / (dst.stem + tail))))
    return moves


# ---------------------------------------------------------------------------
# 执行：后台任务入口
# ---------------------------------------------------------------------------


@dataclass
class OrganizeSummary:
    """一次整理的结论（日志与接口响应共用）。"""

    library_id: int
    renamed: int = 0  # 成功改名归位的主文件数
    sidecars_renamed: int = 0  # 跟随改名的附属文件数
    already_ok: int = 0  # 本就符合规范
    skipped: int = 0  # 计划阶段跳过（原因见预览）
    removed_dirs: int = 0  # 搬空后清理掉的目录数
    errors: list[str] = field(default_factory=list)


class _MoveError(Exception):
    """单个文件改名失败。message 是完整中文句子，直接进 errors。"""


async def organize_library(library_id: int) -> OrganizeSummary:
    """整理一个库（后台任务入口；自开会话，不向外抛异常）。

    执行时重新计算计划（不信任预览快照），逐文件"改名 → 台账随迁"收口。
    """
    from movieclaw_api.services.library_scan import is_scanning

    summary = OrganizeSummary(library_id=library_id)
    if library_id in _organizing:
        summary.errors.append("该库已有整理在进行中")
        return summary
    if is_scanning(library_id):
        summary.errors.append("该库正在扫描中，请等待扫描完成后再整理")
        return summary
    _organizing.add(library_id)
    _progress[library_id] = (0, 0)
    try:
        return await _organize(library_id, summary)
    except Exception:  # noqa: BLE001 -- 后台任务兜底
        logger.exception("媒体库 #%s 整理时发生未知错误", library_id)
        summary.errors.append("整理中断：发生未知错误（详见后端日志）")
        return summary
    finally:
        _organizing.discard(library_id)
        _progress.pop(library_id, None)
        _last[library_id] = (utcnow(), summary)


async def _organize(library_id: int, summary: OrganizeSummary) -> OrganizeSummary:
    db = get_database()
    async with db.session() as session:
        library = await session.get(Library, library_id)
        if library is None:
            summary.errors.append("媒体库不存在（可能已被删除）")
            return summary
        plan = await build_organize_plan(session, library)
        summary.already_ok = plan.already_ok
        summary.skipped = len(plan.skips)
        repo = LibraryFileRepository(session)
        roots = [r.rstrip("/") for r in library.root_paths]

        _progress[library_id] = (0, len(plan.renames))
        dirty_parents: set[Path] = set()
        for done, action in enumerate(plan.renames, start=1):
            try:
                await asyncio.to_thread(
                    _move_no_clobber, Path(action.source_path), Path(action.target_path)
                )
            except _MoveError as exc:
                summary.errors.append(str(exc))
                _progress[library_id] = (done, len(plan.renames))
                continue
            # 改名成功立即随迁台账：中途失败不会留下账实不符的批量烂摊子
            container = Path(action.target_path).suffix.lstrip(".").lower() or None
            await repo.relocate(action.file_id, file_path=action.target_path, container=container)
            summary.renamed += 1
            dirty_parents.add(Path(action.source_path).parent)
            for sidecar in action.sidecars:
                try:
                    await asyncio.to_thread(
                        _move_no_clobber, Path(sidecar.source_path), Path(sidecar.target_path)
                    )
                    summary.sidecars_renamed += 1
                except _MoveError as exc:
                    summary.errors.append(f"附属文件改名失败：{exc}")
            _progress[library_id] = (done, len(plan.renames))

        # 只清理被本次整理搬空的目录（及其变空的祖先）：非空即停、绝不删文件，
        # 与整理无关的空目录一概不碰
        summary.removed_dirs = await asyncio.to_thread(_prune_emptied_dirs, dirty_parents, roots)

    logger.info(
        "媒体库 #%s 整理完成：改名归位 %d（附属文件 %d），已规范 %d，跳过 %d，"
        "清理空目录 %d，问题 %d",
        library_id,
        summary.renamed,
        summary.sidecars_renamed,
        summary.already_ok,
        summary.skipped,
        summary.removed_dirs,
        len(summary.errors),
    )
    return summary


def _move_no_clobber(src: Path, dst: Path) -> None:
    """同文件系统内的防覆盖改名（线程池内运行）。

    ``os.rename`` 会静默覆盖已存在的目标，这里用 ``os.link + os.unlink``：
    link 遇到目标已存在会原子失败（EEXIST），杜绝"计划检查后、执行前恰有
    同名文件落地（如入库管线并发硬链）"的覆盖窗口。不支持硬链的文件系统
    （极少数网络挂载）退回"先查存在再 rename"。
    """
    if not src.exists():
        raise _MoveError(f"源文件已不在原位，跳过：{src}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise _MoveError(f"创建目标目录失败（{exc.strerror}）：{dst.parent}") from exc
    try:
        os.link(src, dst)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise _MoveError(f"目标路径已被占用，跳过以免覆盖：{dst}") from exc
        if exc.errno == errno.EXDEV:
            raise _MoveError(f"目标与源不在同一文件系统，无法改名归位：{src} → {dst}") from exc
        if exc.errno in (errno.EPERM, errno.ENOTSUP):
            if dst.exists():
                raise _MoveError(f"目标路径已被占用，跳过以免覆盖：{dst}") from exc
            try:
                os.rename(src, dst)
            except OSError as exc2:
                raise _MoveError(f"改名失败（{exc2.strerror}）：{src} → {dst}") from exc2
            return
        raise _MoveError(f"改名失败（{exc.strerror}）：{src} → {dst}") from exc
    os.unlink(src)


def _prune_emptied_dirs(dirty_parents: set[Path], roots: list[str]) -> int:
    """从被搬空的目录向上清理：目录空则 rmdir 并继续看父级，非空/到根即停。"""
    removed = 0
    root_paths = {Path(r) for r in roots}
    for parent in dirty_parents:
        current = parent
        while current not in root_paths and any(current.is_relative_to(r) for r in root_paths):
            try:
                current.rmdir()  # 非空目录会抛 OSError——这正是"绝不删文件"的保证
            except OSError:
                break
            removed += 1
            current = current.parent
    return removed
