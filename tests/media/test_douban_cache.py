"""豆瓣客户端持久缓存接入测试：只用 MockTransport 与内存桩存储，不出网。

验证的是接入层语义而非 SwrCache 本身（后者见 tests/cache/test_swr.py）：
榜单/详情的原始响应落盘、跨客户端实例（模拟重启）复用、无效详情负缓存。
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from movieclaw_cache import StoredEntry
from movieclaw_media.douban import DoubanClient, DoubanError


class MemoryStore:
    """满足 CacheStore 协议的最小内存实现，跨客户端实例共享。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], StoredEntry] = {}

    async def get(self, namespace: str, key: str) -> StoredEntry | None:
        return self.rows.get((namespace, key))

    async def set(self, namespace: str, key: str, payload: str) -> None:
        self.rows[(namespace, key)] = StoredEntry(
            payload=payload, fetched_at=datetime.now(UTC).replace(tzinfo=None)
        )


def _transport(payload: dict, hits: list[str]) -> httpx.MockTransport:
    async def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.path)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler)


async def test_collection_is_served_from_store_across_client_restart() -> None:
    store = MemoryStore()
    hits: list[str] = []
    payload = {"subject_collection_items": [{"id": "1"}]}

    client = DoubanClient(transport=_transport(payload, hits), store=store)
    assert await client.collection("movie_top250", count=250) == payload
    assert await client.collection("movie_top250", count=250) == payload
    await client.aclose()
    assert len(hits) == 1

    # 模拟进程重启：新客户端、同一份持久存储，不再发起 HTTP 请求
    reborn = DoubanClient(transport=_transport(payload, hits), store=store)
    assert await reborn.collection("movie_top250", count=250) == payload
    await reborn.aclose()
    assert len(hits) == 1
    assert ("douban", "collection:movie_top250:250") in store.rows


async def test_detail_is_cached_and_invalid_detail_is_negative_cached() -> None:
    store = MemoryStore()
    hits: list[str] = []
    detail = {"id": "26266893", "title": "流浪地球"}

    client = DoubanClient(transport=_transport(detail, hits), store=store)
    assert await client.detail("26266893") == detail
    assert await client.detail("26266893") == detail
    await client.aclose()
    assert len(hits) == 1

    # 无效条目（豆瓣返回空 JSON）：报可读错误，且负缓存期内不再回源
    bad_hits: list[str] = []
    client = DoubanClient(transport=_transport({}, bad_hits), store=store)
    with pytest.raises(DoubanError, match="未返回有效"):
        await client.detail("999")
    with pytest.raises(DoubanError, match="未返回有效"):
        await client.detail("999")
    await client.aclose()
    assert len(bad_hits) == 1


async def test_transient_failure_is_not_cached() -> None:
    """瞬时故障（HTTP 500）抛错但不落盘，恢复后下一次请求即可成功。"""
    store = MemoryStore()
    responses = [httpx.Response(500), httpx.Response(200, json={"id": "1", "title": "x"})]

    async def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    client = DoubanClient(transport=httpx.MockTransport(handler), store=store)
    with pytest.raises(DoubanError, match="访问豆瓣详情失败"):
        await client.detail("1")
    assert await client.detail("1") == {"id": "1", "title": "x"}
    await client.aclose()
