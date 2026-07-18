"""Agent 工具挂点：声明 + 执行器的绑定。

工具的「声明」（ToolDefinition，暴露给模型）与「执行」（handler，服务端
真正干活）在此绑成一个单元。领域工具（站点搜索、提交下载等）在上层
注册成 AgentTool 列表传给 AgentRunner——本库不感知任何具体领域。

handler 约定：
- 入参是已通过 JSON Schema 校验的参数 dict；
- 返回喂回模型的文本（结果的紧凑文字表达，模型可读即可）；
- 抛出异常不会中断 agent loop：runner 会把异常信息作为失败结果回喂模型。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from movieclaw_llm import ToolDefinition


@dataclass(frozen=True)
class AgentTool:
    """一个可执行的 Agent 工具。"""

    definition: ToolDefinition
    handler: Callable[[dict], Awaitable[str]]

    @property
    def name(self) -> str:
        return self.definition.name
