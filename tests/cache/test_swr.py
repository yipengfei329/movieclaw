"""SwrCache 单元测试：双 TTL、后台刷新、失败降级、负缓存、并发单飞。

全部用内存桩存储，不碰数据库；时间流逝通过回拨存储里的 fetched_at 模拟，
不 sleep、不 mock 时钟。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from movieclaw_cache import StoredEntry, SwrCache

FRESH = 60.0
STALE = 3600.0


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class FakeStore:
    """内存版 CacheStore，可回拨 fetched_at 模拟时间流逝。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], StoredEntry] = {}

    async def get(self, namespace: str, key: str) -> StoredEntry | None:
        return self.rows.get((namespace, key))

    async def set(self, namespace: str, key: str, payload: str) -> None:
        self.rows[(namespace, key)] = StoredEntry(payload=payload, fetched_at=_utcnow())

    def age(self, key: str, seconds: float, namespace: str = "t") -> None:
        """把某条记录的抓取时间拨旧 seconds 秒。"""
        entry = self.rows[(namespace, key)]
        self.rows[(namespace, key)] = StoredEntry(
            payload=entry.payload,
            fetched_at=entry.fetched_at - timedelta(seconds=seconds),
        )


class BrokenStore:
    """读写都抛错的存储：验证缓存层故障不拖垮功能。"""

    async def get(self, namespace: str, key: str) -> StoredEntry | None:
        raise RuntimeError("storage down")

    async def set(self, namespace: str, key: str, payload: str) -> None:
        raise RuntimeError("storage down")


def _counting_factory(values: list[Any]):
    """依次返回 values 的工厂；Exception 项会被抛出。calls 记录调用次数。"""
    calls: list[int] = []

    async def factory() -> Any:
        calls.append(1)
        value = values[min(len(calls), len(values)) - 1]
        if isinstance(value, Exception):
            raise value
        return value

    return factory, calls


async def test_miss_fetches_then_serves_fresh_without_refetch() -> None:
    store = FakeStore()
    cache = SwrCache(store, "t")
    factory, calls = _counting_factory([{"v": 1}])

    first = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    second = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)

    assert first == second == {"v": 1}
    assert len(calls) == 1
    assert ("t", "k") in store.rows


async def test_stale_serves_old_value_and_refreshes_in_background() -> None:
    store = FakeStore()
    cache = SwrCache(store, "t")
    factory, calls = _counting_factory([{"v": 1}, {"v": 2}])
    await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    store.age("k", FRESH + 1)

    stale = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    assert stale == {"v": 1}  # 立即返回旧值，不等回源
    await cache.drain()
    assert len(calls) == 2  # 后台已刷新

    refreshed = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    assert refreshed == {"v": 2}
    assert len(calls) == 2


async def test_beyond_stale_blocks_and_refetches() -> None:
    store = FakeStore()
    cache = SwrCache(store, "t")
    factory, calls = _counting_factory([{"v": 1}, {"v": 2}])
    await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    store.age("k", STALE + 1)

    value = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    assert value == {"v": 2}
    assert len(calls) == 2


async def test_fetch_failure_without_usable_cache_raises() -> None:
    cache = SwrCache(FakeStore(), "t")
    factory, _ = _counting_factory([RuntimeError("上游不可用")])
    with pytest.raises(RuntimeError, match="上游不可用"):
        await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)


async def test_background_refresh_failure_keeps_old_value() -> None:
    store = FakeStore()
    cache = SwrCache(store, "t")
    factory, calls = _counting_factory([{"v": 1}, RuntimeError("刷新失败"), {"v": 3}])
    await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    store.age("k", FRESH + 1)

    stale = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    await cache.drain()
    assert stale == {"v": 1}
    assert len(calls) == 2

    # 刷新失败不污染缓存：下次访问仍拿旧值并再次尝试后台刷新
    again = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    await cache.drain()
    assert again == {"v": 1}
    assert len(calls) == 3


async def test_refresh_returning_none_does_not_overwrite_old_value() -> None:
    """上游偶发返回空数据时，后台刷新不许把好缓存降级成负缓存。"""
    store = FakeStore()
    cache = SwrCache(store, "t")
    factory, _ = _counting_factory([{"v": 1}, None])
    await cache.get_or_fetch(
        "k", fresh_ttl=FRESH, stale_ttl=STALE, negative_ttl=600, factory=factory
    )
    store.age("k", FRESH + 1)

    await cache.get_or_fetch(
        "k", fresh_ttl=FRESH, stale_ttl=STALE, negative_ttl=600, factory=factory
    )
    await cache.drain()

    value = await cache.get_or_fetch(
        "k", fresh_ttl=FRESH, stale_ttl=STALE, negative_ttl=600, factory=factory
    )
    assert value == {"v": 1}
    await cache.drain()


async def test_negative_cache_suppresses_refetch_until_expiry() -> None:
    store = FakeStore()
    cache = SwrCache(store, "t")
    factory, calls = _counting_factory([None, None, {"v": 1}])

    first = await cache.get_or_fetch(
        "k", fresh_ttl=FRESH, stale_ttl=STALE, negative_ttl=600, factory=factory
    )
    second = await cache.get_or_fetch(
        "k", fresh_ttl=FRESH, stale_ttl=STALE, negative_ttl=600, factory=factory
    )
    assert first is None and second is None
    assert len(calls) == 1  # 负缓存期内不再回源

    store.age("k", 601)
    third = await cache.get_or_fetch(
        "k", fresh_ttl=FRESH, stale_ttl=STALE, negative_ttl=600, factory=factory
    )
    assert third is None  # 负缓存过期后重试，上游仍无数据
    assert len(calls) == 2


async def test_none_store_falls_through_to_factory_every_time() -> None:
    cache = SwrCache(None, "t")
    factory, calls = _counting_factory([{"v": 1}, {"v": 2}])
    first = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    second = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    assert (first, second) == ({"v": 1}, {"v": 2})
    assert len(calls) == 2


async def test_broken_store_degrades_to_direct_fetch() -> None:
    """存储读写全挂时，退化为直连上游，功能不受影响。"""
    cache = SwrCache(BrokenStore(), "t")
    factory, calls = _counting_factory([{"v": 1}])
    value = await cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=factory)
    assert value == {"v": 1}
    assert len(calls) == 1


async def test_concurrent_misses_fetch_only_once() -> None:
    store = FakeStore()
    cache = SwrCache(store, "t")
    calls: list[int] = []

    async def slow_factory() -> dict[str, int]:
        calls.append(1)
        await asyncio.sleep(0.01)
        return {"v": 1}

    results = await asyncio.gather(
        *(
            cache.get_or_fetch("k", fresh_ttl=FRESH, stale_ttl=STALE, factory=slow_factory)
            for _ in range(5)
        )
    )
    assert all(result == {"v": 1} for result in results)
    assert len(calls) == 1
