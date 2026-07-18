"""AgentRunRegistry：后台生命周期、广播回放、取消与过期清理。"""

from __future__ import annotations

import asyncio

import pytest

from movieclaw_agent import AgentDone, AgentEvent, AgentStartParams
from movieclaw_api.exceptions import NotFoundException
from movieclaw_api.services.agent_runs import AgentRunRegistry


class _ControlledRunner:
    """首事件后暂停，测试可精确控制断线期间何时继续发布。"""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def start(self, params, *, run_id=None):
        yield AgentEvent(type="agent_start", run_id=run_id, provider="测试", model="model")
        await self.release.wait()
        yield AgentEvent(type="text_delta", run_id=run_id, delta="完成")
        yield AgentEvent(type="agent_done", run_id=run_id, result=AgentDone(text="完成"))


async def _next(registry: AgentRunRegistry, run_id: str, after: int = 0):
    return await registry.get_events(run_id, after, timeout_seconds=1)


async def test_history_resume_and_two_subscribers_receive_same_events() -> None:
    registry = AgentRunRegistry()
    runner = _ControlledRunner()
    run_id = registry.start(runner, AgentStartParams(input="测试"))
    try:
        first, terminal = await _next(registry, run_id)
        assert [item.sequence for item in first] == [1]
        assert not terminal

        # 模拟首个 SSE 已断开：新订阅从 0 仍能回放同一条历史，且不影响任务。
        replay, terminal = await _next(registry, run_id)
        assert [item.event.type for item in replay] == ["agent_start"]
        assert not terminal

        # 两个独立订阅者都从序号 1 续传，不会像 Queue 一样互相抢事件。
        subscriber_a = asyncio.create_task(_next(registry, run_id, 1))
        subscriber_b = asyncio.create_task(_next(registry, run_id, 1))
        runner.release.set()
        (events_a, terminal_a), (events_b, terminal_b) = await asyncio.gather(
            subscriber_a, subscriber_b
        )
        assert [item.sequence for item in events_a] == [2, 3]
        assert [item.sequence for item in events_b] == [2, 3]
        assert terminal_a and terminal_b
    finally:
        await registry.close()


async def test_cancel_publishes_cancelled_terminal() -> None:
    registry = AgentRunRegistry()
    runner = _ControlledRunner()
    run_id = registry.start(runner, AgentStartParams(input="测试"))
    try:
        first, _ = await _next(registry, run_id)
        await registry.cancel(run_id)
        events, terminal = await _next(registry, run_id, first[-1].sequence)
        assert terminal
        assert [item.event.type for item in events] == ["agent_cancelled"]
        # 终态后再次取消仍成功，不新增第二个取消事件。
        await registry.cancel(run_id)
        all_events, terminal = await _next(registry, run_id)
        assert terminal
        assert [item.event.type for item in all_events].count("agent_cancelled") == 1
    finally:
        await registry.close()


async def test_unexpected_runner_error_becomes_visible_terminal() -> None:
    class BrokenRunner:
        async def start(self, params, *, run_id=None):
            yield AgentEvent(type="agent_start", run_id=run_id)
            raise RuntimeError("测试异常")

    registry = AgentRunRegistry()
    run_id = registry.start(BrokenRunner(), AgentStartParams(input="测试"))
    try:
        events, terminal = await _next(registry, run_id)
        if not terminal:
            events, terminal = await _next(registry, run_id)
        assert terminal
        assert events[-1].event.type == "agent_error"
        assert "测试异常" in events[-1].event.error
    finally:
        await registry.close()


async def test_completed_run_expires_after_retention() -> None:
    now = [100.0]

    class DoneRunner:
        async def start(self, params, *, run_id=None):
            yield AgentEvent(
                type="agent_done",
                run_id=run_id,
                result=AgentDone(text="完成"),
            )

    registry = AgentRunRegistry(retention_seconds=10, clock=lambda: now[0])
    run_id = registry.start(DoneRunner(), AgentStartParams(input="测试"))
    try:
        _, terminal = await _next(registry, run_id)
        assert terminal
        now[0] = 111.0
        with pytest.raises(NotFoundException, match="已过期"):
            await _next(registry, run_id)
    finally:
        await registry.close()
