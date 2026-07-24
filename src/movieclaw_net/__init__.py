"""movieclaw_net —— 统一网络出口层（叶子基础包，任何域包都可依赖）。

对外三件事：
- ``egress_transport(service)``：按服务标签构造带代理路由与熔断的 transport；
- ``apply_egress_config(...)``：应用用户的网络配置（代理模式/地址/每服务开关）；
- ``CircuitOpenError``：熔断快速失败异常，供上层翻译成用户可读的引导信息。
"""

from movieclaw_net.breaker import (
    CircuitBreaker,
    CircuitOpenError,
    get_breaker,
    reset_all_breakers,
)
from movieclaw_net.egress import (
    PROXY_SCHEMES,
    EgressConfig,
    EgressScope,
    EgressTransport,
    ProxyMode,
    apply_egress_config,
    browser_tls_context,
    egress_transport,
    env_proxy_url,
    get_egress_config,
    resolve_proxy_url,
)

__all__ = [
    "PROXY_SCHEMES",
    "CircuitBreaker",
    "CircuitOpenError",
    "EgressConfig",
    "EgressScope",
    "EgressTransport",
    "ProxyMode",
    "apply_egress_config",
    "browser_tls_context",
    "egress_transport",
    "env_proxy_url",
    "get_breaker",
    "get_egress_config",
    "resolve_proxy_url",
    "reset_all_breakers",
]
