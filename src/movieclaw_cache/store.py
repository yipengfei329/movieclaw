"""持久缓存的存取协议（L2 的存储抽象）。

存储层被刻意设计成傻瓜化：只做「namespace + key → JSON 文本 + 抓取时间」
的原始读写，**不理解 TTL、不做过期判断**。新鲜期/可用期策略全部由上层的
``SwrCache`` 按调用方给定的参数在代码里计算——将来调整策略不需要动数据，
也不需要数据库迁移。

SQLite 实现在 ``movieclaw_db.stores.SqlCacheStore``（cache_entry 表），由
装配层注入；这是结构化 Protocol，任何满足两个方法签名的对象都可用（测试
里用内存字典即可）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class StoredEntry:
    """存储层返回的一条原始缓存记录。"""

    payload: str
    """缓存值的 JSON 文本。"""

    fetched_at: datetime
    """回源抓取时间；按项目约定为 naive UTC，新鲜度由上层据此计算。"""


class CacheStore(Protocol):
    """持久缓存存储协议：get/set 两个方法，别的都不该有。"""

    async def get(self, namespace: str, key: str) -> StoredEntry | None: ...

    async def set(self, namespace: str, key: str, payload: str) -> None: ...
