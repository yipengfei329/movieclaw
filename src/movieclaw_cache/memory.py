"""进程内 TTL 缓存（L1：发现页聚合结果、TMDB 类型表等全局只读数据专用）。

项目没有 Redis 等外部缓存设施；这类数据是「全站共享、分钟级新鲜度即可」
的只读结果，用最简单的进程内字典即可满足。多实例部署时各实例独立缓存，
代价只是各自回源一次，可以接受。需要跨重启存活的数据请用 ``SwrCache``。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

# 缺失哨兵：缓存值本身可能是 None/空列表，不能拿真值判断命中与否
_MISS = object()


class AsyncTTLCache:
    """按 key 缓存异步工厂函数的结果，过期后重新回源。

    并发防击穿：同一 key 的并发请求只有第一个真正回源，其余在 per-key 锁上
    等待并复用结果。过期条目不做主动清理——key 空间是固定的几个页面/类型表
    加有限的详情条目，不存在无限增长问题。
    """

    def __init__(self) -> None:
        self._values: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_or_set(
        self, key: str, ttl: float, factory: Callable[[], Awaitable[Any]]
    ) -> Any:
        hit = self._lookup(key)
        if hit is not _MISS:
            return hit
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            # 等锁期间可能已被并发请求填好，二次检查避免重复回源
            hit = self._lookup(key)
            if hit is not _MISS:
                return hit
            value = await factory()
            self._values[key] = (time.monotonic() + ttl, value)
            return value

    def _lookup(self, key: str) -> Any:
        entry = self._values.get(key)
        if entry is None:
            return _MISS
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            return _MISS
        return value

    def clear(self) -> None:
        """清空全部缓存（测试与热更配置时用）。"""
        self._values.clear()
        self._locks.clear()
