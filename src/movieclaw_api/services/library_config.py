"""媒体库配置服务：库的增删改查、默认库不变量与入库路径推导（L1）。

媒体库是"我拥有哪些影视内容、放在哪里"的权威定义（docs/design/library.md）。
L1 阶段它的唯一消费者是投递：订阅/手动下载按"入库到哪个库"确定 save_path
（``derive_save_path``：主根 + 规范条目目录名）。入库管线、扫描等能力
在 L2/L3 接入。

首启种子：``seed_default_libraries`` 在库表为空时创建"电影库/剧集库"两个
默认库，根路径落在 data/library/ 下（与 SQLite 同卷，Docker 部署天然持久化；
NAS 用户在设置页把根路径改到真实媒体盘即可）。
"""

from __future__ import annotations

import logging
import posixpath
import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_api.exceptions import BadRequestException, ConflictException, NotFoundException
from movieclaw_db.engine import get_database
from movieclaw_db.models.library import Library
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_media.models import MediaKind

logger = logging.getLogger("movieclaw_api.library_config")

# 条目目录名里的文件系统保留字符（跨 ext4/NTFS/APFS 的并集），统一替换为空格。
# Plex/Emby 对目录名的解析只依赖 "标题 (年份)" 结构，替换不影响识别。
_FORBIDDEN_CHARS = re.compile(r'[\\/:*?"<>|]')


def sanitize_folder_name(name: str) -> str:
    """把标题清洗成安全的目录名：替换保留字符、折叠空白、去首尾点与空格。"""
    cleaned = _FORBIDDEN_CHARS.sub(" ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "未命名"


def derive_save_path(library: Library, *, title: str, year: int | None) -> str | None:
    """由库推导入库保存路径：``{主根}/{title} ({year})``。

    电影与剧集同构（剧集的 Season 子目录是 L2 整理器的职责，投递阶段
    下载器只需要落到条目目录）。库没有根路径时返回 None（调用方回落
    到下载器默认目录）。路径用 POSIX 分隔符拼接——save_path 是给
    下载器所在环境用的，movieclaw 部署面向 Linux/NAS/Docker。
    """
    root = library.primary_root
    if not root:
        return None
    folder = sanitize_folder_name(title)
    if year is not None:
        folder = f"{folder} ({year})"
    return posixpath.join(root.rstrip("/"), folder)


class LibraryConfigService:
    """媒体库配置的业务服务。绑定一个数据库会话。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = LibraryRepository(session)

    # -- 查询 --------------------------------------------------------------

    async def list_all(self, *, kind: str | None = None) -> list[Library]:
        """返回全部库（可按类型过滤）。"""
        return await self._repo.list_all(kind=kind)

    async def get(self, library_id: int) -> Library:
        """按 id 获取；不存在抛 404。"""
        row = await self._repo.get(library_id)
        if row is None:
            raise NotFoundException(f"媒体库不存在：id={library_id}")
        return row

    async def resolve_for_subscription(self, library_id: int | None, kind: str) -> Library | None:
        """解析订阅/投递实际使用的库：显式指定优先，否则该类型的默认库。

        显式指定的库已被删除（外键 SET NULL 前的竞态）或类型没有任何库时
        返回 None——调用方回落到下载器默认目录，不阻断投递。
        """
        if library_id is not None:
            row = await self._repo.get(library_id)
            if row is not None:
                return row
        return await self._repo.get_default(kind)

    # -- 写入 --------------------------------------------------------------

    def _validate(self, *, name: str, root_paths: list[str]) -> list[str]:
        """公共校验：名称非空、根路径非空且均为绝对路径。返回清洗后的根列表。"""
        if not name.strip():
            raise BadRequestException("库名称不能为空")
        cleaned = [p.strip() for p in root_paths if p.strip()]
        if not cleaned:
            raise BadRequestException("至少需要一个根路径（第一个为主根，新入库落在这里）")
        for path in cleaned:
            if not path.startswith("/"):
                raise BadRequestException(f"根路径必须是绝对路径：{path}")
        if len(set(cleaned)) != len(cleaned):
            raise BadRequestException("根路径存在重复项")
        return cleaned

    async def _assert_name_available(self, name: str, *, exclude_id: int | None = None) -> None:
        existing = await self._repo.get_by_name(name)
        if existing is not None and existing.id != exclude_id:
            raise ConflictException(f"名称「{name}」已被使用，请换一个")

    @staticmethod
    async def _refresh_watcher() -> None:
        """库/根路径变更后重建实时监听（监听器未启动时为 no-op）。"""
        from movieclaw_api.services.library_watch import get_library_watcher

        watcher = get_library_watcher()
        if watcher is not None:
            await watcher.refresh_watches()

    async def create(self, *, name: str, kind: MediaKind, root_paths: list[str]) -> Library:
        """新增一个库。该类型尚无默认库时自动成为默认。"""
        cleaned = self._validate(name=name, root_paths=root_paths)
        await self._assert_name_available(name)
        row = await self._repo.create(name=name.strip(), kind=kind.value, root_paths=cleaned)
        await self._refresh_watcher()
        return row

    async def update(self, library_id: int, *, name: str, root_paths: list[str]) -> Library:
        """更新名称与根路径。kind 创建后不可改（订阅按类型挂库）。"""
        await self.get(library_id)
        cleaned = self._validate(name=name, root_paths=root_paths)
        await self._assert_name_available(name, exclude_id=library_id)
        updated = await self._repo.update(library_id, name=name.strip(), root_paths=cleaned)
        assert updated is not None  # get() 已确认存在
        await self._refresh_watcher()
        return updated

    async def set_default(self, library_id: int) -> Library:
        """设为该类型的默认库（订阅/手动下载不选库时用它）。"""
        ok = await self._repo.set_default(library_id)
        if not ok:
            raise NotFoundException(f"媒体库不存在：id={library_id}")
        return await self.get(library_id)

    async def delete(self, library_id: int) -> None:
        """删除库。挂在它上面的订阅回落到该类型默认库（外键 SET NULL）。"""
        row = await self.get(library_id)
        await self._repo.delete(library_id)
        await self._refresh_watcher()
        logger.info("媒体库「%s」已删除，其订阅将回落到该类型的默认库", row.name)


# ---------------------------------------------------------------------------
# 首启种子（lifespan 启动时调用）
# ---------------------------------------------------------------------------


async def seed_default_libraries(base_dir: str) -> None:
    """库表为空时种子"电影库/剧集库"两个默认库（幂等：非空即跳过）。

    根路径放在 ``{base_dir}/movies|tv``（base_dir 来自 LIBRARY_DEFAULT_ROOT，
    默认与 SQLite 同卷，Docker 部署开箱可用）；真实 NAS 用户应在
    「设置 → 媒体库」把根路径改到媒体盘。
    """
    async with get_database().session() as session:
        repo = LibraryRepository(session)
        if await repo.count():
            return
        base = str(Path(base_dir).resolve())
        movie = await repo.create(
            name="电影库", kind=MediaKind.MOVIE.value, root_paths=[posixpath.join(base, "movies")]
        )
        tv = await repo.create(
            name="剧集库", kind=MediaKind.TV.value, root_paths=[posixpath.join(base, "tv")]
        )
        logger.info(
            "已创建默认媒体库：「%s」（%s）与「%s」（%s）——请在「设置 → 媒体库」"
            "把根路径改到你的媒体目录",
            movie.name,
            movie.primary_root,
            tv.name,
            tv.primary_root,
        )
