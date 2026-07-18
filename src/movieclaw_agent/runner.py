"""AgentRunner —— Agent loop 执行器。

循环语义（与用户逐条确认的设计）：
- 每一步 = 一次流式模型调用；模型输出的工具调用**以内容为准**判断是否
  继续循环——finish_reason 只作参考，因为兼容端点存在「stop 但带调用」
  「tool_calls 但数组为空」两类在案怪癖（vLLM/Kimi 等）；
- 有工具调用 → 顺序执行每一个（参数先过 JSON Schema 校验），结果作为
  tool 消息回喂，进入下一步；无工具调用 → STOP，正常结束；
- 工具的校验失败与执行异常都不中断循环：错误文本作为失败结果回喂，
  让模型自行修正（pi-agent / Claude Code 同款韧性设计）；
- max_steps（默认 200）防失控；错误一律以 agent_error 事件收尾，不抛异常。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

from movieclaw_agent.events import AgentDone, AgentEvent, AgentStartParams, AgentToolResult
from movieclaw_agent.prompts import build_system_prompt
from movieclaw_agent.toolkit import AgentTool
from movieclaw_llm import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    LlmError,
    LlmRouter,
    TokenUsage,
    ToolCall,
    validate_tool_call,
)

logger = logging.getLogger(__name__)

#: tool_result 事件里输出的截断长度（完整输出仍进对话上下文喂给模型）
_EVENT_OUTPUT_LIMIT = 2000


class AgentRunner:
    """绑定一个 LlmRouter 与一组工具的 Agent loop 执行器。"""

    def __init__(
        self,
        router: LlmRouter,
        tools: list[AgentTool] | None = None,
        *,
        max_steps: int = 200,
        on_message: Callable[[ChatMessage, ChatResponse | None], Awaitable[None]]
        | None = None,
    ) -> None:
        self._router = router
        self._tools = tools or []
        self._tools_by_name = {t.name: t for t in self._tools}
        self._max_steps = max_steps
        # 定稿消息回调（会话持久化的挂点）：每当一条消息内容完全确定——
        # 中间步的 assistant（含 tool_calls）、每条 tool 结果、以及终答
        # assistant——各调用一次。流式 delta 不经过这里（只落定稿不落增量）。
        # assistant 消息附带所属 ChatResponse 供取 model/usage/finish_reason。
        self._on_message = on_message

    async def start(
        self,
        params: AgentStartParams,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """启动一次运行，产出 Agent 事件流。

        ``run_id`` 允许 API 编排层在创建后台任务前预先分配运行编号，这样
        创建接口返回的编号与随后所有事件中的编号完全一致。领域层直接调用
        时仍可省略，由 runner 自行生成，保持原有用法不变。
        """
        run_id = run_id or uuid.uuid4().hex[:12]
        started = time.monotonic()

        # 路由解析放在发事件之前：解析失败（没配供应商/模型不存在）
        # 也要以 agent_error 事件的形式告知前端，而不是断流
        try:
            provider, model_id = self._router.resolve(params.model)
        except LlmError as exc:
            yield AgentEvent(type="agent_error", run_id=run_id, error=str(exc))
            return

        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=params.system_prompt or build_system_prompt())
        ]
        messages.extend(params.history)
        messages.append(ChatMessage(role="user", content=params.input))
        definitions = [t.definition for t in self._tools]

        yield AgentEvent(
            type="agent_start", run_id=run_id, provider=provider.name, model=model_id
        )

        usage = TokenUsage()
        for step in range(1, self._max_steps + 1):
            request = ChatRequest(
                model=params.model,
                messages=messages,
                tools=definitions or None,
                settings=params.settings,
            )

            final: ChatResponse | None = None
            async for event in self._router.chat_stream(request):
                if event.type == "thinking_delta":
                    yield AgentEvent(type="thinking_delta", run_id=run_id, delta=event.delta)
                elif event.type == "text_delta":
                    yield AgentEvent(type="text_delta", run_id=run_id, delta=event.delta)
                elif event.type == "toolcall_start":
                    # 名称一确定就上报，前端从这一刻起即可展示「正在调用 xx 工具」
                    yield AgentEvent(
                        type="tool_call_start", run_id=run_id, tool_call=event.tool_call
                    )
                elif event.type == "toolcall_delta":
                    # 参数 JSON 逐片上报；tool_call_id 让前端把增量归到正确的调用
                    yield AgentEvent(
                        type="tool_call_delta",
                        run_id=run_id,
                        delta=event.delta,
                        tool_call_id=event.tool_call.id if event.tool_call else None,
                    )
                elif event.type == "toolcall_end":
                    yield AgentEvent(type="tool_call", run_id=run_id, tool_call=event.tool_call)
                elif event.type == "error":
                    logger.warning("Agent 运行失败 run=%s：%s", run_id, event.partial.error)
                    yield AgentEvent(
                        type="agent_error",
                        run_id=run_id,
                        error=event.partial.error or "模型调用失败，原因未知",
                    )
                    return
                elif event.type == "done":
                    final = event.partial

            if final is None:
                yield AgentEvent(
                    type="agent_error", run_id=run_id, error="模型流异常终止，未返回结果"
                )
                return

            usage = _add_usage(usage, final.usage)

            # 循环判据：以 tool_calls 内容为准（finish_reason 只作参考）——
            # 覆盖「stop 但带调用」与「tool_calls 但数组为空」两类兼容怪癖
            if not final.tool_calls:
                await self._notify(final.to_message(), final)
                yield AgentEvent(
                    type="agent_done",
                    run_id=run_id,
                    result=AgentDone(
                        text=final.content,
                        thinking=final.thinking,
                        finish_reason=final.finish_reason,
                        usage=usage,
                        steps=step,
                        model=final.model,
                        provider=final.provider,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                    ),
                )
                return

            # assistant 消息（含 tool_calls）入上下文，随后逐个执行工具
            messages.append(final.to_message())
            await self._notify(final.to_message(), final)
            for tc in final.tool_calls:
                result = await self._execute_tool(tc, definitions)
                yield AgentEvent(
                    type="tool_result",
                    run_id=run_id,
                    tool_result=AgentToolResult(
                        tool_call_id=result.tool_call_id,
                        name=result.name,
                        output=result.output[:_EVENT_OUTPUT_LIMIT],
                        is_error=result.is_error,
                        elapsed_ms=result.elapsed_ms,
                    ),
                )
                tool_message = ChatMessage(
                    role="tool",
                    content=result.output,
                    tool_call_id=tc.id,
                    name=tc.name,
                )
                messages.append(tool_message)
                await self._notify(tool_message, None)

        logger.warning("Agent 达到最大步数上限 run=%s steps=%d", run_id, self._max_steps)
        yield AgentEvent(
            type="agent_error",
            run_id=run_id,
            error=f"已达到最大执行步数上限（{self._max_steps} 步）仍未完成，运行终止。"
            "请把任务拆小后重试，或检查是否陷入了循环。",
        )

    async def _notify(self, message: ChatMessage, response: ChatResponse | None) -> None:
        """调用定稿消息回调；持久化失败只告警，不中断正在进行的运行。"""
        if self._on_message is None:
            return
        try:
            await self._on_message(message, response)
        except Exception:  # noqa: BLE001 - 落盘故障不应打断模型循环
            logger.exception("Agent 消息持久化回调失败（本条消息可能未落盘）")

    async def _execute_tool(self, tc: ToolCall, definitions: list) -> AgentToolResult:
        """执行单个工具调用：校验 → 执行；任何失败都转为回喂文本，不抛异常。"""
        started = time.monotonic()
        args, err = validate_tool_call(definitions, tc)
        if err is not None:
            output, is_error = err, True
        else:
            tool = self._tools_by_name[tc.name]  # validate 已确认工具存在
            try:
                output, is_error = await tool.handler(args), False
            except Exception as exc:  # noqa: BLE001 - 工具异常回喂模型，不中断 loop
                logger.warning("工具执行失败 tool=%s：%s", tc.name, exc)
                output, is_error = f"工具执行失败：{exc}", True
        return AgentToolResult(
            tool_call_id=tc.id,
            name=tc.name,
            output=output,
            is_error=is_error,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )


def _add_usage(total: TokenUsage, step: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=total.prompt_tokens + step.prompt_tokens,
        completion_tokens=total.completion_tokens + step.completion_tokens,
        total_tokens=total.total_tokens + step.total_tokens,
        cache_read_tokens=total.cache_read_tokens + step.cache_read_tokens,
    )
