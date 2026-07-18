"""AgentRunner loop：工具执行循环、兼容怪癖判据、错误回喂、步数上限。"""

from __future__ import annotations

import json

from movieclaw_agent import AgentRunner, AgentStartParams, AgentTool
from movieclaw_llm import (
    ChatMessage,
    ChatResponse,
    LlmProviderConfig,
    LlmRouter,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from movieclaw_llm.base import BaseLlmProtocol
from movieclaw_llm.models import ChatStreamEvent
from movieclaw_llm.protocols import PROTOCOLS

SEARCH_TOOL_DEF = ToolDefinition(
    name="search",
    description="搜索资源",
    parameters={
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    },
)

# 各测试用例共享的探针：记录工具入参与每步请求
_probe: dict = {}


def make_runner(protocol_cls, monkeypatch, *, handler=None, max_steps=200) -> AgentRunner:
    monkeypatch.setitem(PROTOCOLS, "openai_chat", protocol_cls)
    _probe.clear()
    _probe["requests"] = []

    async def default_handler(args: dict) -> str:
        _probe["tool_args"] = args
        return "找到 3 条资源：A / B / C"

    router = LlmRouter(
        [
            LlmProviderConfig(
                name="测试百炼",
                provider_type="bailian",
                api_key="sk-x",
                default_model="qwen3.7-max",
                is_default=True,
            )
        ]
    )
    tools = [AgentTool(definition=SEARCH_TOOL_DEF, handler=handler or default_handler)]
    return AgentRunner(router, tools=tools, max_steps=max_steps)


class ToolLoopProtocol(BaseLlmProtocol):
    """两步流：首步发起工具调用，见到 tool 消息后给最终答复。"""

    #: 首步 done 的 finish_reason（子类覆盖以模拟兼容怪癖）
    first_finish = "tool_calls"
    #: 首步的工具调用（子类覆盖以模拟坏参数/未知工具）
    first_calls = [ToolCall(id="c1", name="search", arguments={"q": "沙丘"})]

    async def chat(self, request, model_id):  # pragma: no cover
        raise NotImplementedError

    async def chat_stream(self, request, model_id):
        _probe["requests"].append(request)
        snap = ChatResponse(model=model_id, provider=self.config.name)
        has_tool_msg = any(m.role == "tool" for m in request.messages)
        yield ChatStreamEvent(type="start", partial=snap)
        if not has_tool_msg:
            for tc in self.first_calls:
                # 与真实协议层一致的三段式：start（仅 id/name）→ delta 分片 → end
                yield ChatStreamEvent(
                    type="toolcall_start",
                    tool_call=ToolCall(id=tc.id, name=tc.name),
                    partial=snap,
                )
                args_json = json.dumps(tc.arguments, ensure_ascii=False)
                mid = len(args_json) // 2
                for piece in (args_json[:mid], args_json[mid:]):
                    yield ChatStreamEvent(
                        type="toolcall_delta",
                        delta=piece,
                        tool_call=ToolCall(id=tc.id, name=tc.name, raw_arguments=args_json),
                        partial=snap,
                    )
                yield ChatStreamEvent(type="toolcall_end", tool_call=tc, partial=snap)
            yield ChatStreamEvent(
                type="done",
                partial=ChatResponse(
                    tool_calls=self.first_calls or None,
                    finish_reason=self.first_finish,
                    usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                    model=model_id,
                    provider=self.config.name,
                ),
            )
        else:
            yield ChatStreamEvent(type="text_delta", delta="最终答复", partial=snap)
            yield ChatStreamEvent(
                type="done",
                partial=ChatResponse(
                    content="最终答复",
                    finish_reason="stop",
                    usage=TokenUsage(prompt_tokens=20, completion_tokens=7, total_tokens=27),
                    model=model_id,
                    provider=self.config.name,
                ),
            )

    async def test_connection(self):  # pragma: no cover
        raise NotImplementedError

    async def close(self):
        pass


async def collect(runner: AgentRunner, params: AgentStartParams):
    return [e async for e in runner.start(params)]


async def test_tool_loop_two_steps(monkeypatch):
    runner = make_runner(ToolLoopProtocol, monkeypatch)
    events = await collect(runner, AgentStartParams(input="找沙丘"))
    assert [e.type for e in events] == [
        "agent_start",
        "tool_call_start",
        "tool_call_delta",
        "tool_call_delta",
        "tool_call",
        "tool_result",
        "text_delta",
        "agent_done",
    ]
    # 三段式工具调用事件：start 只带名称，delta 归属正确且拼出完整参数 JSON
    start = events[1]
    assert start.tool_call.name == "search" and start.tool_call.arguments == {}
    deltas = [e for e in events if e.type == "tool_call_delta"]
    assert all(e.tool_call_id == "c1" for e in deltas)
    assert "".join(e.delta for e in deltas) == '{"q": "沙丘"}'
    # 工具收到校验后的参数
    assert _probe["tool_args"] == {"q": "沙丘"}
    # 工具回执
    tr = next(e.tool_result for e in events if e.type == "tool_result")
    assert tr.name == "search" and not tr.is_error
    assert "找到 3 条资源" in tr.output
    # 终态：两步、usage 累计、最终正文
    done = events[-1].result
    assert done.steps == 2
    assert done.text == "最终答复"
    assert done.usage.total_tokens == 42  # 15 + 27
    # 第二步请求的上下文形态：system + user + assistant(带调用) + tool 结果
    round2 = _probe["requests"][1]
    assert [m.role for m in round2.messages] == ["system", "user", "assistant", "tool"]
    assert round2.messages[0].text().startswith("你是 movieclaw 的执行 Agent")
    tool_msg = round2.messages[-1]
    assert tool_msg.tool_call_id == "c1"
    assert "找到 3 条资源" in tool_msg.text()


async def test_stop_finish_reason_with_tool_calls_still_loops(monkeypatch):
    """兼容怪癖①：finish_reason=stop 但带工具调用 → 以内容为准，照常执行。"""

    class QuirkProtocol(ToolLoopProtocol):
        first_finish = "stop"

    runner = make_runner(QuirkProtocol, monkeypatch)
    events = await collect(runner, AgentStartParams(input="x"))
    assert events[-1].type == "agent_done"
    assert events[-1].result.steps == 2


async def test_tool_calls_finish_reason_with_empty_calls_ends(monkeypatch):
    """兼容怪癖②：finish_reason=tool_calls 但数组为空 → 安全终止，不空转。"""

    class EmptyCallsProtocol(ToolLoopProtocol):
        first_calls = []

    runner = make_runner(EmptyCallsProtocol, monkeypatch)
    events = await collect(runner, AgentStartParams(input="x"))
    assert events[-1].type == "agent_done"
    assert events[-1].result.steps == 1


async def test_invalid_tool_args_fed_back_as_error(monkeypatch):
    """参数校验失败：不中断循环，错误描述作为失败结果回喂模型。"""

    class BadArgsProtocol(ToolLoopProtocol):
        first_calls = [ToolCall(id="c1", name="search", arguments={"q": 123})]

    runner = make_runner(BadArgsProtocol, monkeypatch)
    events = await collect(runner, AgentStartParams(input="x"))
    tr = next(e.tool_result for e in events if e.type == "tool_result")
    assert tr.is_error
    assert "不符合定义" in tr.output
    # 循环继续走完并正常结束
    assert events[-1].type == "agent_done"
    # 错误文本确实进了第二步的上下文
    assert "不符合定义" in _probe["requests"][1].messages[-1].text()


async def test_tool_handler_exception_fed_back(monkeypatch):
    """工具执行抛异常：转为失败结果回喂，不中断循环。"""

    async def broken_handler(args: dict) -> str:
        raise RuntimeError("站点连接超时")

    runner = make_runner(ToolLoopProtocol, monkeypatch, handler=broken_handler)
    events = await collect(runner, AgentStartParams(input="x"))
    tr = next(e.tool_result for e in events if e.type == "tool_result")
    assert tr.is_error
    assert "站点连接超时" in tr.output
    assert events[-1].type == "agent_done"


async def test_max_steps_guard(monkeypatch):
    """模型永远要求调工具 → 达到步数上限后以 agent_error 明确终止。"""

    class ForeverProtocol(ToolLoopProtocol):
        async def chat_stream(self, request, model_id):
            _probe["requests"].append(request)
            snap = ChatResponse(model=model_id, provider=self.config.name)
            tc = ToolCall(id=f"c{len(_probe['requests'])}", name="search", arguments={"q": "x"})
            yield ChatStreamEvent(type="toolcall_end", tool_call=tc, partial=snap)
            yield ChatStreamEvent(
                type="done",
                partial=ChatResponse(
                    tool_calls=[tc], finish_reason="tool_calls",
                    model=model_id, provider=self.config.name,
                ),
            )

    runner = make_runner(ForeverProtocol, monkeypatch, max_steps=3)
    events = await collect(runner, AgentStartParams(input="x"))
    assert events[-1].type == "agent_error"
    assert "最大执行步数上限（3 步）" in events[-1].error
    assert sum(1 for e in events if e.type == "tool_result") == 3


async def test_stream_error_ends_with_agent_error(monkeypatch):
    class BrokenProtocol(ToolLoopProtocol):
        async def chat_stream(self, request, model_id):
            snap = ChatResponse(model=model_id, provider=self.config.name)
            yield ChatStreamEvent(type="start", partial=snap)
            yield ChatStreamEvent(type="text_delta", delta="部分", partial=snap)
            yield ChatStreamEvent(
                type="error",
                partial=ChatResponse(
                    content="部分", finish_reason="error", error="连接模型服务失败"
                ),
            )

    runner = make_runner(BrokenProtocol, monkeypatch)
    events = await collect(runner, AgentStartParams(input="x"))
    assert [e.type for e in events] == ["agent_start", "text_delta", "agent_error"]
    assert "连接模型服务失败" in events[-1].error


async def test_routing_failure_yields_agent_error_without_start():
    events = [
        e
        async for e in AgentRunner(LlmRouter([])).start(AgentStartParams(input="x"))
    ]
    assert [e.type for e in events] == ["agent_error"]
    assert "没有任何已启用" in events[0].error


async def test_system_prompt_override_and_history(monkeypatch):
    """system_prompt 覆盖生效；history 按序插在 system 与本轮 input 之间。"""
    runner = make_runner(ToolLoopProtocol, monkeypatch)
    params = AgentStartParams(
        input="本轮问题",
        system_prompt="自定义系统词",
        history=[
            ChatMessage(role="user", content="上轮问题"),
            ChatMessage(role="assistant", content="上轮回答"),
        ],
    )
    await collect(runner, params)
    first = _probe["requests"][0]
    assert [m.role for m in first.messages] == ["system", "user", "assistant", "user"]
    assert first.messages[0].text() == "自定义系统词"
    assert first.messages[-1].text() == "本轮问题"
    # 工具声明随请求下发
    assert first.tools[0].name == "search"
