"""LLM 调用错误分类体系。

分类的核心目的是给 agent loop 提供重试决策依据：``retryable`` 标记该类
错误是否值得退避重试（限流、网络抖动），还是应该立即失败（key 无效、
参数错误、内容审查）。协议实现负责把 SDK 异常翻译成这套体系，翻译时
附带中文说明——本项目面向非开发者部署，错误信息必须能看懂。
"""

from __future__ import annotations


class LlmError(Exception):
    """LLM 调用错误基类。"""

    #: 该类错误是否值得重试（由子类覆盖）
    retryable: bool = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        self.provider = provider
        prefix = f"[{provider}] " if provider else ""
        super().__init__(f"{prefix}{message}")


class LlmAuthError(LlmError):
    """认证失败（401/403）：API Key 无效或无权限，重试无意义。"""


class LlmRateLimitError(LlmError):
    """触发限流（429）：可按 retry_after 退避后重试。"""

    retryable = True

    def __init__(
        self, message: str, *, provider: str | None = None, retry_after: float | None = None
    ) -> None:
        self.retry_after = retry_after
        super().__init__(message, provider=provider)


class LlmConnectError(LlmError):
    """网络连接失败或超时：可重试。"""

    retryable = True


class LlmRequestError(LlmError):
    """请求参数错误（400）：如模型不存在、上下文超长，重试无意义。"""


class LlmContentFilterError(LlmError):
    """内容审查拦截：输入或输出触发供应商的合规审查（百炼国内场景常见）。"""


class LlmRoutingError(LlmError):
    """路由失败：model 引用无法解析到任何可用的供应商实例。"""
