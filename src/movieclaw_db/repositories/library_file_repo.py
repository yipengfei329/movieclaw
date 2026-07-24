from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.library_file import LibraryFile


class LibraryFileRepository:
    """库存台账的数据访问层。

    ``file_path`` 是全局唯一键：入库/扫描的写入统一走 ``upsert_by_path``
    ——同一路径重复发现（重扫、重复入库）更新而非重复插入。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- 查询 --------------------------------------------------------------

    async def get_by_path(self, file_path: str) -> LibraryFile | None:
        result = await self._session.execute(
            select(LibraryFile).where(LibraryFile.file_path == file_path)
        )
        return result.scalar_one_or_none()

    async def list_by_library(self, library_id: int) -> list[LibraryFile]:
        """某库的全部台账（含 missing 行，展示层自行标注）。"""
        result = await self._session.execute(
            select(LibraryFile).where(LibraryFile.library_id == library_id).order_by(LibraryFile.id)
        )
        return list(result.scalars().all())

    async def list_unidentified(self, *, library_id: int | None = None) -> list[LibraryFile]:
        """待识别清单：落了账但没挂上身份锚的文件。"""
        stmt = select(LibraryFile).where(LibraryFile.media_item_id.is_(None))  # type: ignore[union-attr]
        if library_id is not None:
            stmt = stmt.where(LibraryFile.library_id == library_id)
        result = await self._session.execute(stmt.order_by(LibraryFile.file_path))
        return list(result.scalars().all())

    async def find_by_size(self, library_id: int, size_bytes: int) -> list[LibraryFile]:
        """同库同尺寸的台账行——改名归并的候选池（尺寸是改名/移动的不变量）。"""
        result = await self._session.execute(
            select(LibraryFile).where(
                LibraryFile.library_id == library_id,
                LibraryFile.size_bytes == size_bytes,
            )
        )
        return list(result.scalars().all())

    async def owned_units(self, media_item_id: int) -> set[tuple[int, int]]:
        """某条目在库的期望单元集合（库存 H）——wanted 生成跳过已有的依据。

        只算"在位"的文件（missing 的不算拥有）。
        """
        result = await self._session.execute(
            select(LibraryFile.season_number, LibraryFile.episode_number)
            .where(
                LibraryFile.media_item_id == media_item_id,
                LibraryFile.missing_since.is_(None),  # type: ignore[union-attr]
            )
            .distinct()
        )
        return {(row[0], row[1]) for row in result.all()}

    # -- 写入 --------------------------------------------------------------

    async def upsert_by_path(self, row: LibraryFile) -> LibraryFile:
        """按 file_path 幂等写入：已有则整体更新（文件回归时清 missing 标记）。"""
        existing = await self.get_by_path(row.file_path)
        if existing is None:
            self._session.add(row)
            await self._session.commit()
            await self._session.refresh(row)
            return row
        existing.library_id = row.library_id
        existing.media_item_id = row.media_item_id
        existing.season_number = row.season_number
        existing.episode_number = row.episode_number
        existing.size_bytes = row.size_bytes
        existing.container = row.container
        existing.resolution = row.resolution
        existing.video_codec = row.video_codec
        existing.hdr = row.hdr
        existing.bit_depth = row.bit_depth
        existing.duration_seconds = row.duration_seconds
        existing.bit_rate = row.bit_rate
        existing.media_source = row.media_source
        existing.release_group = row.release_group
        existing.source = row.source
        existing.site_id = row.site_id
        existing.torrent_id = row.torrent_id
        existing.unidentified_reason = row.unidentified_reason
        existing.missing_since = None  # 再次发现即在位
        existing.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(existing)
        return existing

    async def relocate(self, file_id: int, *, file_path: str, container: str | None) -> None:
        """改名归并：把台账行迁到新路径，身份锚与介质信息原样保留。"""
        row = await self._session.get(LibraryFile, file_id)
        if row is None:
            return
        row.file_path = file_path
        row.container = container
        row.missing_since = None
        row.updated_at = utcnow()
        await self._session.commit()

    async def claim_identity(
        self, file_id: int, *, media_item_id: int, season_number: int, episode_number: int
    ) -> LibraryFile | None:
        """人工认领：给未识别文件挂上身份锚。不存在返回 None。"""
        row = await self._session.get(LibraryFile, file_id)
        if row is None:
            return None
        row.media_item_id = media_item_id
        row.season_number = season_number
        row.episode_number = episode_number
        row.unidentified_reason = None  # 已有身份，失败原因随之失义
        row.updated_at = utcnow()
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def mark_missing(self, file_id: int, *, since: datetime | None = None) -> None:
        """对账：标记文件消失（不删记录）。"""
        row = await self._session.get(LibraryFile, file_id)
        if row is None:
            return
        row.missing_since = since or utcnow()
        row.updated_at = utcnow()
        await self._session.commit()

    async def delete(self, file_id: int) -> bool:
        """删除一条台账（仅供待识别清单的"忽略此文件"，不动磁盘）。"""
        row = await self._session.get(LibraryFile, file_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True
