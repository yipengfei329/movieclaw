"""工具调用参数校验 —— agent loop 的韧性地基。

模型输出的工具参数不可信（幻觉字段、类型错误、残缺 JSON）。校验失败
不抛异常中断 loop，而是返回一段中文错误描述，调用方应把它作为
tool 结果回喂给模型，让模型自行修正后重试（pi-ai 的 validateToolCall
同款思路）。
"""

from __future__ import annotations

import jsonschema

from movieclaw_llm.models import ToolCall, ToolDefinition


def validate_tool_call(
    tools: list[ToolDefinition], tool_call: ToolCall
) -> tuple[dict | None, str | None]:
    """校验模型发起的工具调用。

    返回 ``(参数, None)`` 表示通过；``(None, 错误描述)`` 表示失败，
    错误描述应作为 tool 消息回喂模型。
    """
    tool = next((t for t in tools if t.name == tool_call.name), None)
    if tool is None:
        names = ", ".join(t.name for t in tools) or "（无）"
        return None, f"工具「{tool_call.name}」不存在，可用工具：{names}"
    if tool_call.parse_error:
        return None, f"工具参数解析失败：{tool_call.parse_error}，请重新以合法 JSON 输出参数"
    try:
        jsonschema.validate(tool_call.arguments, tool.parameters)
    except jsonschema.ValidationError as exc:
        path = ".".join(str(p) for p in exc.absolute_path) or "(根)"
        return None, f"工具参数不符合定义（字段 {path}）：{exc.message}，请修正后重试"
    return tool_call.arguments, None
