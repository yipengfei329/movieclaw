"""协议实现注册：协议 id → 实现类。新增协议（如 anthropic）时在此登记。"""

from movieclaw_llm.base import BaseLlmProtocol
from movieclaw_llm.protocols.openai_chat import OpenAIChatProtocol

PROTOCOLS: dict[str, type[BaseLlmProtocol]] = {
    "openai_chat": OpenAIChatProtocol,
}

__all__ = ["PROTOCOLS", "OpenAIChatProtocol"]
