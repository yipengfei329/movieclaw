"""系统通用缓存模块：进程内 L1 + 持久化 L2（stale-while-revalidate）。

本包是全系统共享的基础设施，不依赖任何业务包（movieclaw_db 通过结构化
协议接入存储实现，依赖方向始终是「业务包 → 本包」）。

准入边界（防止它退化成垃圾抽屉）：只放**可随时整体删除、可从上游重建**的
派生数据（豆瓣/TMDB 等上游 API 响应）。领域数据有自己的表、二进制大块走
磁盘文件、会话/任务状态是事实源——都不属于这里。
"""

from movieclaw_cache.memory import AsyncTTLCache
from movieclaw_cache.store import CacheStore, StoredEntry
from movieclaw_cache.swr import SwrCache

__all__ = [
    "AsyncTTLCache",
    "CacheStore",
    "StoredEntry",
    "SwrCache",
]
