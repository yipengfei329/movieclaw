"""双 TTL 持久缓存读取器：stale-while-revalidate（下称 SWR）语义的核心实现。

每个缓存值有两个生命阶段（时长由调用方按 key 类型给定）：

- **新鲜期（fresh_ttl）内**：直接返回，不碰上游；
- **可用期（stale_ttl）内**：立即返回旧值，同时后台单飞刷新——用户永远
  不为回源等待，上游故障时自动降级到旧数据而不是报错；
- **超出可用期或无缓存**：阻塞回源（per-key 锁防击穿），成功后落盘。

负缓存：factory 返回 ``None`` 表示「上游确认无此数据」（如无效的豆瓣 ID），
落盘为标记并在 negative_ttl 内直接返回 None，防止坏 key 被反复回源；上游
瞬时故障应当抛异常而不是返回 None，异常不会被缓存。

容错原则：缓存层永远不该把功能搞挂——存储读写失败一律按未命中/跳过落盘
处理并记日志，最坏情况退化为无持久缓存的直连行为。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from movieclaw_cache.store import CacheStore

logger = logging.getLogger("movieclaw_cache.swr")

# 负缓存落盘标记。正常业务 payload 不允许使用该保留字段。
_NEGATIVE = {"__negative__": True}

Factory = Callable[[], Awaitable[Any]]


def _utcnow() -> datetime:
    """naive UTC，与 movieclaw_db 的时间约定一致（见 models/base.py）。"""
    return datetime.now(UTC).replace(tzinfo=None)


class SwrCache:
    """绑定一个 namespace 的 SWR 缓存读取器。

    值必须可 JSON 序列化（缓存原始上游响应而非解析后的模型，解析逻辑迭代
    时无需清缓存）。per-key 锁与后台任务集不做清理——key 空间是有限的榜单
    加缓慢增长的详情条目，与 AsyncTTLCache 同一取舍。
    """

    def __init__(self, store: CacheStore | None, namespace: str) -> None:
        # store 为 None 时整体退化为无缓存直连（装配层未接入持久存储的场景）
        self._store = store
        self._namespace = namespace
        self._locks: dict[str, asyncio.Lock] = {}
        self._refreshing: set[str] = set()
        self._background: set[asyncio.Task[None]] = set()

    async def get_or_fetch(
        self,
        key: str,
        *,
        fresh_ttl: float,
        stale_ttl: float,
        factory: Factory,
        negative_ttl: float = 0.0,
    ) -> Any:
        """按 SWR 语义取值；factory 返回 None 且 negative_ttl > 0 时启用负缓存。"""
        if self._store is None:
            return await factory()

        hit = await self._read(key)
        if hit is not None:
            age, value, negative = hit
            if negative:
                if age < negative_ttl:
                    return None
            elif age < fresh_ttl:
                return value
            elif age < stale_ttl:
                self._spawn_refresh(key, factory)
                return value

        # 无缓存或已超出可用期：阻塞回源，per-key 锁防止并发击穿
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = await self._read(key)
            if hit is not None:
                age, value, negative = hit
                if negative and age < negative_ttl:
                    return None
                if not negative and age < stale_ttl:
                    return value
            value = await factory()
            await self._write(key, value)
            return value

    async def drain(self) -> None:
        """等待所有后台刷新结束（测试断言与优雅停机用）。"""
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)

    def _spawn_refresh(self, key: str, factory: Factory) -> None:
        """后台单飞刷新：同一 key 已有刷新在跑时直接跳过。"""
        if key in self._refreshing:
            return
        self._refreshing.add(key)
        task = asyncio.create_task(self._refresh(key, factory))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _refresh(self, key: str, factory: Factory) -> None:
        try:
            value = await factory()
            # 后台刷新拿到 None 不覆盖旧值：上游偶发抽风返回空数据时，
            # 保住手里可用的旧缓存比忠实记录一次"无数据"更重要。
            if value is not None:
                await self._write(key, value)
        except Exception as exc:
            logger.warning(
                "缓存后台刷新失败，继续沿用旧数据：%s/%s（%s）",
                self._namespace, key, exc,
            )
        finally:
            self._refreshing.discard(key)

    async def _read(self, key: str) -> tuple[float, Any, bool] | None:
        """读存储并解码，返回 (age 秒, 值, 是否负缓存)；任何异常按未命中处理。"""
        assert self._store is not None
        try:
            stored = await self._store.get(self._namespace, key)
            if stored is None:
                return None
            value = json.loads(stored.payload)
        except Exception as exc:
            logger.warning(
                "读持久缓存失败，按未命中处理：%s/%s（%s）", self._namespace, key, exc
            )
            return None
        age = (_utcnow() - stored.fetched_at).total_seconds()
        negative = isinstance(value, dict) and value.get("__negative__") is True
        return age, value if not negative else None, negative

    async def _write(self, key: str, value: Any) -> None:
        assert self._store is not None
        payload = json.dumps(_NEGATIVE if value is None else value, ensure_ascii=False)
        try:
            await self._store.set(self._namespace, key, payload)
        except Exception as exc:
            logger.warning(
                "写持久缓存失败，本次结果不落盘：%s/%s（%s）", self._namespace, key, exc
            )
