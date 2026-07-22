"""进程内 Agent 运行注册表：后台执行、事件回放与广播订阅。

这里刻意不用 ``asyncio.Queue``：队列中的一条消息只会被一个消费者取走，
无法同时满足多个 SSE 订阅者，也无法让断线客户端回放历史。本模块为每次运行
维护一份只追加的事件日志，订阅者各自持有序号游标；``asyncio.Condition`` 只
负责在新事件到达时唤醒等待者，消息本身始终以日志为准。

注册表仅在单个 API 进程内有效。浏览器或 SSE 连接断开不会影响后台任务，但
进程重启会丢失所有运行；若未来支持多 worker，应把这一层替换为 Redis 等共享
存储，而不是改变 AgentRunner 或 SSE 事件协议。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from movieclaw_agent import AgentEvent, AgentRunner, AgentStartParams
from movieclaw_api.exceptions import BadRequestException, NotFoundException

logger = logging.getLogger("movieclaw_api.agent_runs")

TERMINAL_EVENT_TYPES = {"agent_done", "agent_error", "agent_cancelled"}
DEFAULT_RETENTION_SECONDS = 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class StoredAgentEvent:
    """带运行内递增序号的事件；序号直接用作 SSE ``id``。"""

    sequence: int
    event: AgentEvent


@dataclass(slots=True)
class _AgentRun:
    """一次运行的全部进程内状态，由 AgentRunRegistry 在同一事件循环中保护。"""

    run_id: str
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    events: list[StoredAgentEvent] = field(default_factory=list)
    task: asyncio.Task[None] | None = None
    terminal: bool = False
    completed_at: float | None = None
    #: 终态钩子：运行进入终态（done/error/cancelled）后调用一次，
    #: 供会话持久化做收尾（停心跳、补配对、清运行标记）
    on_terminal: Callable[[AgentEvent], Awaitable[None]] | None = None


class AgentRunRegistry:
    """管理后台 Agent 任务，并为每次运行提供可回放的广播事件日志。

    关键保证：
    1. 创建接口持有 task 强引用，因此 HTTP/SSE 请求结束不会取消 Agent；
    2. 发布时先追加日志、再唤醒全部订阅者，重连不会漏掉通知窗口内的事件；
    3. 每个订阅者按自己的 sequence 读取，慢客户端不会阻塞生产者；
    4. 所有退出路径都补齐终态事件，SSE 不会无限等待一个已消失的任务。
    """

    def __init__(
        self,
        *,
        retention_seconds: float = DEFAULT_RETENTION_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._retention_seconds = retention_seconds
        self._clock = clock
        self._runs: dict[str, _AgentRun] = {}
        self._closing = False

    def start(
        self,
        runner: AgentRunner,
        params: AgentStartParams,
        *,
        on_terminal: Callable[[AgentEvent], Awaitable[None]] | None = None,
    ) -> str:
        """分配运行编号并把 runner 放入后台执行，立即返回编号。"""
        if self._closing:
            raise RuntimeError("Agent 运行注册表正在关闭，无法创建新运行")
        self._prune_expired()
        run_id = uuid.uuid4().hex[:12]
        run = _AgentRun(run_id=run_id, on_terminal=on_terminal)
        self._runs[run_id] = run
        run.task = asyncio.create_task(
            self._execute(run, runner, params),
            name=f"agent-run-{run_id}",
        )
        logger.info("Agent 后台运行已创建 run=%s", run_id)
        return run_id

    async def get_events(
        self,
        run_id: str,
        after_sequence: int,
        *,
        timeout_seconds: float,
    ) -> tuple[list[StoredAgentEvent], bool]:
        """返回游标后的事件；暂无事件时等待通知，超时返回空列表供 SSE 发心跳。

        第二个返回值表示运行是否已进入终态。调用方应先发送本批事件，再在
        ``terminal=True`` 且批次已追平时关闭连接。
        """
        if after_sequence < 0:
            raise BadRequestException("SSE 事件游标不能为负数")
        run = self._get_run(run_id)
        async with run.condition:
            if after_sequence > len(run.events):
                raise BadRequestException(f"SSE 事件游标 {after_sequence} 超出当前事件范围")
            if after_sequence == len(run.events) and not run.terminal:
                try:
                    await asyncio.wait_for(
                        run.condition.wait_for(
                            lambda: after_sequence < len(run.events) or run.terminal
                        ),
                        timeout=timeout_seconds,
                    )
                except TimeoutError:
                    return [], False
            return list(run.events[after_sequence:]), run.terminal

    async def cancel(self, run_id: str) -> None:
        """幂等取消一次运行，并在取消 task 前先落下可回放的终态事件。

        先写事件很重要：创建接口刚返回、后台协程尚未获得调度时，直接
        ``task.cancel()`` 可能让协程一次都不执行，因而没有机会在 except 中
        补 ``agent_cancelled``，订阅者会永久等待。
        """
        run = self._get_run(run_id)
        if run.terminal:
            return
        await self._publish(
            run,
            AgentEvent(type="agent_cancelled", run_id=run.run_id),
        )
        if run.task is not None and not run.task.done():
            logger.info("用户请求取消 Agent 运行 run=%s", run_id)
            run.task.cancel()

    async def close(self) -> None:
        """应用关闭时取消并等待全部活动任务，避免遗留悬空协程。"""
        self._closing = True
        tasks = [run.task for run in self._runs.values() if run.task and not run.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()
        logger.info("Agent 运行注册表已关闭，活动任务均已回收")

    async def _execute(
        self,
        run: _AgentRun,
        runner: AgentRunner,
        params: AgentStartParams,
    ) -> None:
        """消费 runner 事件并写入日志，兜住取消、异常和异常断流三种出口。"""
        try:
            async for event in runner.start(params, run_id=run.run_id):
                await self._publish(run, event)
            if not run.terminal:
                await self._publish(
                    run,
                    AgentEvent(
                        type="agent_error",
                        run_id=run.run_id,
                        error="Agent 运行异常结束，未返回终态事件",
                    ),
                )
        except asyncio.CancelledError:
            if not run.terminal:
                await self._publish(
                    run,
                    AgentEvent(type="agent_cancelled", run_id=run.run_id),
                )
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务必须转成可见终态，不能静默消失
            logger.exception("Agent 后台运行发生未知错误 run=%s", run.run_id)
            if not run.terminal:
                await self._publish(
                    run,
                    AgentEvent(
                        type="agent_error",
                        run_id=run.run_id,
                        error=f"Agent 运行发生未知错误：{exc}",
                    ),
                )
        finally:
            run.task = None

    async def _publish(self, run: _AgentRun, event: AgentEvent) -> None:
        """原子追加事件并广播通知；终态之后的迟到事件直接忽略。"""
        became_terminal = False
        async with run.condition:
            if run.terminal:
                return
            run.events.append(StoredAgentEvent(sequence=len(run.events) + 1, event=event))
            if event.type in TERMINAL_EVENT_TYPES:
                run.terminal = True
                run.completed_at = self._clock()
                became_terminal = True
                logger.info("Agent 后台运行已结束 run=%s status=%s", run.run_id, event.type)
            run.condition.notify_all()
        # 终态钩子在锁外调用：收尾涉及文件与数据库 IO，不应阻塞事件广播；
        # terminal 置位保证本钩子最多触发一次
        if became_terminal and run.on_terminal is not None:
            try:
                await run.on_terminal(event)
            except Exception:  # noqa: BLE001 - 收尾失败不能影响事件流的终态语义
                logger.exception("Agent 运行终态钩子执行失败 run=%s", run.run_id)

    def _get_run(self, run_id: str) -> _AgentRun:
        self._prune_expired()
        run = self._runs.get(run_id)
        if run is None:
            raise NotFoundException("Agent 运行不存在或事件历史已过期")
        return run

    def _prune_expired(self) -> None:
        """惰性清理超过保留期的终态运行；活动运行永不在这里删除。"""
        cutoff = self._clock() - self._retention_seconds
        expired = [
            run_id
            for run_id, run in self._runs.items()
            if run.completed_at is not None and run.completed_at <= cutoff
        ]
        for run_id in expired:
            del self._runs[run_id]
        if expired:
            logger.info("已清理 %d 条过期 Agent 运行历史", len(expired))


_registry: AgentRunRegistry | None = None


def init_agent_run_registry() -> AgentRunRegistry:
    """为当前 FastAPI 生命周期初始化唯一的运行注册表。"""
    global _registry
    _registry = AgentRunRegistry()
    return _registry


def get_agent_run_registry() -> AgentRunRegistry:
    """取得已初始化的运行注册表；仅供 lifespan 启动后的请求使用。"""
    if _registry is None:
        raise RuntimeError("Agent 运行注册表尚未初始化")
    return _registry


async def close_agent_run_registry() -> None:
    """关闭并清空当前生命周期的注册表。"""
    global _registry
    if _registry is not None:
        await _registry.close()
        _registry = None
