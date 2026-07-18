"""SqlCacheStore / CacheRepository 集成测试（真实 SQLite 临时库）。

覆盖：存取往返、覆盖写刷新 fetched_at、按时间清理，以及关键的重启存活
语义——新建 SwrCache 实例（模拟进程重启）后仍能命中同一份落盘缓存。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import SQLModel

from movieclaw_cache import SwrCache
from movieclaw_db.engine import dispose_db, init_db
from movieclaw_db.models.base import utcnow
from movieclaw_db.models.cache_entry import CacheEntry
from movieclaw_db.repositories.cache_repo import CacheRepository
from movieclaw_db.stores import SqlCacheStore


@pytest.fixture
async def db(tmp_path):
    """临时 SQLite 库：直接用模型元数据建表（迁移链另有专项验证）。"""
    database = init_db(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with database.engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield database
    await dispose_db()


async def test_store_roundtrip_and_overwrite(db) -> None:
    store = SqlCacheStore(database=db)

    assert await store.get("douban", "detail:1") is None

    await store.set("douban", "detail:1", '{"title": "流浪地球"}')
    entry = await store.get("douban", "detail:1")
    assert entry is not None
    assert entry.payload == '{"title": "流浪地球"}'
    first_fetched_at = entry.fetched_at

    await store.set("douban", "detail:1", '{"title": "流浪地球2"}')
    entry = await store.get("douban", "detail:1")
    assert entry.payload == '{"title": "流浪地球2"}'
    assert entry.fetched_at >= first_fetched_at

    # namespace 隔离：同 key 不同域互不可见
    assert await store.get("tmdb", "detail:1") is None


async def test_purge_only_removes_rows_older_than_cutoff(db) -> None:
    async with db.session() as session:
        repo = CacheRepository(session)
        await repo.upsert("douban", "fresh", "{}")
        session.add(
            CacheEntry(
                namespace="douban",
                cache_key="ancient",
                payload="{}",
                fetched_at=utcnow() - timedelta(days=40),
            )
        )
        await session.commit()

    async with db.session() as session:
        deleted = await CacheRepository(session).purge_older_than(
            utcnow() - timedelta(days=30)
        )
    assert deleted == 1

    store = SqlCacheStore(database=db)
    assert await store.get("douban", "ancient") is None
    assert await store.get("douban", "fresh") is not None


async def test_cache_survives_swr_instance_restart(db) -> None:
    """新建 SwrCache（模拟进程重启）后不再回源——持久缓存的核心价值。"""
    calls: list[int] = []

    async def factory() -> dict[str, int]:
        calls.append(1)
        return {"v": 1}

    first = SwrCache(SqlCacheStore(database=db), "douban")
    assert await first.get_or_fetch("k", fresh_ttl=60, stale_ttl=3600, factory=factory) == {"v": 1}

    reborn = SwrCache(SqlCacheStore(database=db), "douban")
    assert await reborn.get_or_fetch("k", fresh_ttl=60, stale_ttl=3600, factory=factory) == {"v": 1}
    assert len(calls) == 1
