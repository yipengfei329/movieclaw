"""统一模型的构造、文本提取与序列化往返。"""

import pytest
from pydantic import ValidationError

from movieclaw_llm import (
    ChatMessage,
    ChatResponse,
    ImagePart,
    LlmProviderConfig,
    TextPart,
    ThinkingPart,
    ToolCall,
)


def test_message_content_accepts_plain_string():
    msg = ChatMessage(role="user", content="你好")
    assert msg.text() == "你好"


def test_message_text_ignores_non_text_parts():
    msg = ChatMessage(
        role="user",
        content=[
            TextPart(text="看看这张海报"),
            ImagePart(url="https://example.com/p.jpg"),
            ThinkingPart(text="不应计入"),
        ],
    )
    assert msg.text() == "看看这张海报"


def test_message_json_round_trip():
    msg = ChatMessage(
        role="assistant",
        content=[ThinkingPart(text="思考"), TextPart(text="答案")],
        tool_calls=[
            ToolCall(id="c1", name="search", arguments={"q": "x"}, raw_arguments='{"q":"x"}')
        ],
    )
    restored = ChatMessage.model_validate_json(msg.model_dump_json())
    assert restored == msg
    assert isinstance(restored.content[0], ThinkingPart)


def test_response_to_message_carries_thinking_and_tool_calls():
    resp = ChatResponse(
        content="正文",
        thinking="推理过程",
        tool_calls=[ToolCall(id="c1", name="f")],
        finish_reason="tool_calls",
    )
    msg = resp.to_message()
    assert msg.role == "assistant"
    assert [type(p) for p in msg.content] == [ThinkingPart, TextPart]
    assert msg.tool_calls[0].id == "c1"


def test_provider_name_rejects_slash():
    with pytest.raises(ValidationError):
        LlmProviderConfig(name="a/b", provider_type="openai", api_key="k")
