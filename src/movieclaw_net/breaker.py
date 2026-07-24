"""按服务标签的进程级熔断器。

解决的问题：外部服务（TMDB、图床、PT 站……）不可达时，每个请求都要等满
「超时 × 重试」才失败（实测发现页拖满 49 秒）。熔断器让「连续失败」之后的
请求**立即失败**，前端能在毫秒级拿到明确错误并引导用户去网络设置排查，
冷却期过后自动放行探测请求，服务恢复即自动闭合。

状态机（经典三态）::

    closed ──连续失败达到阈值──▶ open ──冷却期结束──▶ half-open
      ▲                            ▲                      │
      │◀────── 探测成功 ───────────┼────── 探测失败 ──────┘
                                   └（重新计冷却）

设计取舍
--------
- **只数网络级失败**（连接失败/超时），HTTP 4xx/5xx 是「服务可达但出错」，
  不计入——熔断的语义是"线路不通"，不是"服务出错"。
- 单事件循环内使用，无锁；进程重启即清零，不持久化（线路状态本来就易变）。
- half-open 只放行一个探测请求，其余照旧快速失败，避免恢复瞬间的惊群。
"""

from __future__ import annotations

import time

import httpx


class CircuitOpenError(httpx.TransportError):
    """熔断器打开期间的快速失败异常。

    继承 ``httpx.TransportError``：上层客户端已有的 ``except httpx.HTTPError``
    错误翻译路径无需改动即可捕获；但它**不属于**各客户端 tenacity 重试的
    瞬时异常清单（TimeoutException/ConnectError/…），因此不会被重试拖时间——
    这正是"快速失败"得以贯穿全链路的关键。
    """

    def __init__(self, service: str, retry_after: float) -> None:
        self.service = service
        self.retry_after = max(0.0, retry_after)
        super().__init__(
            f"连接 {service} 已熔断（近期连续失败），约 {int(self.retry_after) + 1} 秒后自动重试。"
            "若持续失败，请到「设置 → 网络」检查代理/镜像配置并使用连通性测试"
        )


class CircuitBreaker:
    """单个服务的熔断状态。时间基于 ``time.monotonic()``，不受系统调时影响。"""

    def __init__(
        self,
        service: str,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._service = service
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._consecutive_failures = 0
        self._opened_at: float | None = None  # None = closed
        self._probing = False  # half-open 期间是否已有探测请求在途

    # ------------------------------------------------------------------
    # 请求前检查
    # ------------------------------------------------------------------
    def before_request(self) -> None:
        """请求发出前调用；熔断打开且不在探测窗口时抛 CircuitOpenError。"""
        if self._opened_at is None:
            return
        elapsed = time.monotonic() - self._opened_at
        if elapsed < self._cooldown:
            raise CircuitOpenError(self._service, self._cooldown - elapsed)
        # 冷却期已过：half-open，只放行一个探测请求
        if self._probing:
            raise CircuitOpenError(self._service, 0.0)
        self._probing = True

    # ------------------------------------------------------------------
    # 请求结果回报
    # ------------------------------------------------------------------
    def record_success(self) -> None:
        """任意 HTTP 响应（含 4xx/5xx）都算线路通，闭合熔断。"""
        self._consecutive_failures = 0
        self._opened_at = None
        self._probing = False

    def record_failure(self) -> None:
        """网络级失败一次；连续达到阈值（或探测失败）时打开/重开熔断。"""
        self._consecutive_failures += 1
        if self._probing or self._consecutive_failures >= self._failure_threshold:
            self._opened_at = time.monotonic()
            self._probing = False

    def reset(self) -> None:
        """人工闭合（连通性测试成功后调用），让业务请求立刻恢复放行。"""
        self.record_success()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None and (
            time.monotonic() - self._opened_at < self._cooldown
        )


# ---------------------------------------------------------------------------
# 进程级注册表：同一服务标签的所有客户端/临时 transport 共享一个熔断器
# ---------------------------------------------------------------------------
_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(service: str) -> CircuitBreaker:
    breaker = _breakers.get(service)
    if breaker is None:
        breaker = CircuitBreaker(service)
        _breakers[service] = breaker
    return breaker


def reset_all_breakers() -> None:
    """清空全部熔断状态（测试隔离、或网络配置变更后给所有服务重新一次机会）。"""
    _breakers.clear()
