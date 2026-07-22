from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from movieclaw_db.models.base import utcnow
from movieclaw_db.models.cache_entry import CacheEntry


class CacheRepository:
    """通用持久缓存表（``cache_entry``）的数据访问层。

    职责边界：只做「(namespace, key) 存取一段 JSON 文本」的原始读写，
    不理解 payload 内容、不做 TTL 判断——过期语义在 ``movieclaw_cache.SwrCache``。
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, namespace: str, key: str) -> CacheEntry | None:
        """读取一条缓存记录；不存在返回 None。"""
        result = await self._session.execute(
            select(CacheEntry).where(CacheEntry.namespace == namespace, CacheEntry.cache_key == key)
        )
        return result.scalar_one_or_none()

    async def upsert(self, namespace: str, key: str, payload: str) -> None:
        """新增或整体覆盖一条缓存记录，并把 fetched_at 刷新为当前时间。"""
        row = await self.get(namespace, key)
        if row is None:
            row = CacheEntry(namespace=namespace, cache_key=key, payload=payload)
            self._session.add(row)
        else:
            row.payload = payload
            row.fetched_at = utcnow()
        await self._session.commit()

    async def purge_older_than(self, cutoff: datetime) -> int:
        """删除抓取时间早于 cutoff 的全部缓存行，返回删除条数（清理任务用）。"""
        result = await self._session.execute(
            delete(CacheEntry).where(CacheEntry.fetched_at < cutoff)
        )
        await self._session.commit()
        return result.rowcount or 0
