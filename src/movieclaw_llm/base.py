"""协议实现的抽象契约。

「协议」对应一种上游 API 形态（如 OpenAI Chat Completions），一个协议
实现可以服务多个供应商预设——OpenAI 官方、百炼、自建 vLLM 都走
openai_chat。只有接入非 OpenAI 形态的 API（如 Anthropic Messages）时
才需要新增协议实现。
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator

from movieclaw_llm.models import (
    ChatRequest,
    ChatResponse,
    ChatStreamEvent,
    LlmProviderConfig,
    ProviderInfo,
    ProviderPreset,
)


class BaseLlmProtocol(abc.ABC):
    """所有协议实现的操作契约。

    约定：
    - model_id 是已由 Router 解析好的具体模型 id，协议实现不再做路由；
    - chat() 失败抛 LlmError 子类（含中文说明）；
    - chat_stream() 内部错误不抛异常，以 type=error 事件收尾，
      让消费方任何时刻都能拿到已生成的 partial。
    """

    def __init__(self, config: LlmProviderConfig, preset: ProviderPreset) -> None:
        self.config = config
        self.preset = preset

    @abc.abstractmethod
    async def chat(self, request: ChatRequest, model_id: str) -> ChatResponse:
        """非流式调用，等待完整结果。"""

    @abc.abstractmethod
    def chat_stream(self, request: ChatRequest, model_id: str) -> AsyncIterator[ChatStreamEvent]:
        """流式调用，产出增量事件序列。"""

    @abc.abstractmethod
    async def test_connection(self) -> ProviderInfo:
        """验证 key 与连通性，返回端点上报的可用模型列表。"""

    @abc.abstractmethod
    async def close(self) -> None:
        """释放底层 HTTP 连接资源。"""
