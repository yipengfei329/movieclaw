"""每站请求限流器——对 PT 站点友好的"礼貌硬下限"。

设计要点
--------
1. **算法**：底层用 ``aiolimiter.AsyncLimiter(max_rate=1, time_period=间隔)``——漏桶、
   桶容量 1，等价于"相邻请求至少隔一个间隔、且无突发"。计时/等待这类容易写错的
   异步细节交给成熟库，本模块只加业务策略。
2. **抖动**：漏桶出来的请求是完全等间距的，部分站点风控会盯"过于规律"的时序。
   因此在闸门后追加一个 **0 ~ interval×jitter 的随机微延迟**——只会让间隔更长、
   绝不低于配置的下限，既打散规律性又不破坏"礼貌硬下限"。
3. **进程级 per-site 注册表**：限流的对象是"对某个站点的总请求速率"，与哪个功能
   （同步 / 搜索 / 详情 / 下载 / 登录）发起无关。因此按 ``site_id`` 全进程共享一个
   限流器，即便每次操作都新建临时 ``HttpClient``，所有请求也从同一个闸门排队通过。

这是"限流 = 秒级请求间距"这一层；它与"调度节奏 = 一次同步多久启动一次"（游标里的
自适应 interval，分钟到小时级）是正交的两层，不要混淆。
"""

from __future__ import annotations

import asyncio
import random

from aiolimiter import AsyncLimiter

# 未在站点 YAML 显式配置 min_request_interval 时使用的默认间隔（秒）。
DEFAULT_MIN_INTERVAL = 3.0
# 抖动比例：在下限之上额外叠加 0 ~ interval×JITTER 的随机延迟。
DEFAULT_JITTER = 0.25


class SiteRateLimiter:
    """单个站点的请求限流器：最小间隔（漏桶）+ 向上抖动。"""

    def __init__(
        self, interval: float, *, jitter: float = DEFAULT_JITTER
    ) -> None:
        # max_rate=1 + time_period=interval ⇒ 每 interval 秒最多放行 1 个请求、无突发
        self._limiter = AsyncLimiter(max_rate=1, time_period=interval)
        self._interval = interval
        self._jitter = jitter

    @property
    def interval(self) -> float:
        return self._interval

    async def acquire(self) -> None:
        """获取一次放行额度：先过漏桶（保证最小间隔），再叠加向上抖动。"""
        await self._limiter.acquire()
        if self._jitter > 0:
            # 只向上加时间：间隔只会更长，不会低于配置的礼貌下限
            await asyncio.sleep(random.uniform(0, self._interval * self._jitter))


# ---------------------------------------------------------------------------
# 进程级 per-site 注册表
# ---------------------------------------------------------------------------

_registry: dict[str, SiteRateLimiter] = {}


def get_site_limiter(
    site_id: str, interval: float | None = None
) -> SiteRateLimiter:
    """取某站点的共享限流器；不存在则按给定间隔懒创建。

    ``interval`` 为 None 时回退到 ``DEFAULT_MIN_INTERVAL``——即站点 YAML 未显式设置
    ``min_request_interval`` 时用默认值；设置了就用它（这是"设定了就用这个"的落点）。
    首次创建后间隔固定（配置在启动期加载，运行期不变）；后续调用复用同一实例，从而
    跨临时客户端保持"上次请求时刻"的连续性。
    """
    limiter = _registry.get(site_id)
    if limiter is None:
        limiter = SiteRateLimiter(interval or DEFAULT_MIN_INTERVAL)
        _registry[site_id] = limiter
    return limiter


def reset_limiters() -> None:
    """清空注册表——仅供测试隔离使用。"""
    _registry.clear()
