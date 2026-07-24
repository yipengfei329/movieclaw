from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from movieclaw_agent import AgentRunner, AgentStartParams, AgentTool
from movieclaw_agent.tools import builtin_tools
from movieclaw_api.core.config import get_settings
from movieclaw_api.exceptions import BadRequestException, NotFoundException
from movieclaw_api.schemas.agent import (
    AgentSessionDetailView,
    AgentSessionListItem,
    AgentSessionRenamePayload,
    AgentStartPayload,
    AgentStartView,
)
from movieclaw_api.schemas.response import ApiResponse, ok
from movieclaw_api.services.agent_runs import get_agent_run_registry
from movieclaw_api.services.agent_session_recorder import AgentSessionRecorder
from movieclaw_api.services.agent_sessions import get_agent_session_store
from movieclaw_api.services.llm_config import acquire_llm_router
from movieclaw_db.engine import get_session
from movieclaw_db.repositories.agent_session_repo import (
    AgentSessionRepository,
    is_running,
)
from movieclaw_llm import ChatMessage

router = APIRouter(prefix="/agent", tags=["agent"])


@lru_cache(maxsize=1)
def get_agent_tools() -> list[AgentTool]:
    """Agent 的工具集（内置基础工具 + 后续领域工具的注册挂点）。

    一期内置 bash / read / write / edit，工作目录取配置的 agent 工作区
    （首次调用时确保目录存在）。领域工具（站点搜索、提交下载等）后续
    在此列表追加。lru_cache 保证进程内构建一次。
    """
    workdir = Path(get_settings().agent_workspace_dir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    return [*builtin_tools(workdir)]


@router.post(
    "/start",
    response_model=ApiResponse[AgentStartView],
    status_code=202,
    summary="创建一次异步 Agent 运行",
)
async def start_agent(
    payload: AgentStartPayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[AgentStartView]:
    """创建后台运行并立即返回编号，执行生命周期不再绑定当前 HTTP 连接。

    会话持久化：每次运行都归属一个服务端会话（新建或续聊）。用户输入先落
    转录文件，运行过程中的定稿消息经 recorder 持续追加；续聊时 LLM 上下文
    从转录重建，前端无需再回传历史。

    路由器在任何会话记录落盘之前组装：读配置、解密 Key 依赖请求级 session；
    组装完成后 runner 只持有进程级 LlmRouter、工具集和纯数据参数，后台执行不
    再访问该 session。尚未配置模型供应商时同步返回 404，且因校验前置，不会残
    留任何空会话记录，便于前端引导用户去设置。
    """
    store = get_agent_session_store()
    repo = AgentSessionRepository(session)

    # 先组装路由器：内部会校验模型供应商是否已配置，未配置时抛 404。必须在任何
    # 会话记录落盘（转录文件 / 索引行）之前完成——否则校验失败时，前端虽然收到
    # 正确的错误提示，磁盘上却已残留一条空会话，下次刷新侧栏会冒出来。
    llm_router = await acquire_llm_router(session)

    if payload.session_id:
        row = await repo.get(payload.session_id)
        if row is None:
            raise NotFoundException("Agent 会话不存在")
        if is_running(row):
            raise BadRequestException("该会话已有正在进行的运行，请先停止或等待完成")
        # 续聊：LLM 上下文从转录文件重建（事实源），忽略前端回传的 history
        history = store.build_history(payload.session_id)
        session_id = payload.session_id
        # 一条 entry 对应一条消息，重建出的历史长度就是文件当前的 entry 数
        entry_count = len(history)
    else:
        header = store.create()
        session_id = header.session_id
        await repo.create(session_id, title=None)
        # 过渡期兼容：老前端在新会话上仍可能带本地历史，只用于本次上下文，
        # 不写入转录（新文件从 0 条 entry 起步）
        history = [ChatMessage(role=m.role, content=m.content) for m in payload.history]
        entry_count = 0

    recorder = AgentSessionRecorder(store, session_id, entry_count=entry_count)
    await recorder.record_user_input(payload.input)

    runner = AgentRunner(
        llm_router,
        tools=get_agent_tools(),
        on_message=recorder.on_message,
    )
    params = AgentStartParams(input=payload.input, history=history, model=payload.model)
    run_id = get_agent_run_registry().start(runner, params, on_terminal=recorder.on_terminal)
    await recorder.begin(run_id)
    return ok(
        AgentStartView(run_id=run_id, session_id=session_id),
        message="Agent 运行已创建",
    )


@router.get(
    "/sessions",
    response_model=ApiResponse[list[AgentSessionListItem]],
    summary="最近会话列表（按最后活跃时间倒序）",
)
async def list_agent_sessions(
    limit: int = 50,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[list[AgentSessionListItem]]:
    """从索引表分页读取；运行状态由 active_run_id + 心跳窗派生，
    running 的条目附带 active_run_id 供前端重新挂上 SSE。"""
    rows = await AgentSessionRepository(session).list_recent(
        limit=min(limit, 200), offset=max(offset, 0)
    )
    return ok([AgentSessionListItem.from_model(row) for row in rows])


@router.get(
    "/sessions/{session_id}",
    response_model=ApiResponse[AgentSessionDetailView],
    summary="会话详情（完整消息回放）",
)
async def get_agent_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[AgentSessionDetailView]:
    """entries 为转录文件的原样投影，渲染约定见 AgentSessionDetailView。"""
    row = await AgentSessionRepository(session).get(session_id)
    if row is None:
        raise NotFoundException("Agent 会话不存在")
    _, entries = get_agent_session_store().read(session_id)
    return ok(
        AgentSessionDetailView(
            session=AgentSessionListItem.from_model(row),
            entries=[e.model_dump(exclude_none=True) for e in entries],
        )
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=ApiResponse[AgentSessionListItem],
    summary="重命名会话",
)
async def rename_agent_session(
    session_id: str,
    payload: AgentSessionRenamePayload,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[AgentSessionListItem]:
    """标题只写索引表（转录文件 append-only，不存可变元数据）。"""
    row = await AgentSessionRepository(session).rename(session_id, payload.title)
    if row is None:
        raise NotFoundException("Agent 会话不存在")
    return ok(AgentSessionListItem.from_model(row), message="会话已重命名")


@router.delete(
    "/sessions/{session_id}",
    response_model=ApiResponse[dict],
    summary="删除会话（转录文件与索引一并删除）",
)
async def delete_agent_session(
    session_id: str,
    session: AsyncSession = Depends(get_session),
) -> ApiResponse[dict]:
    """有正在进行的运行时拒绝删除（先取消运行）；按「先文件后索引」的
    逆序执行——先删文件再删行，即使中途失败也不会出现幽灵会话。"""
    repo = AgentSessionRepository(session)
    row = await repo.get(session_id)
    if row is None:
        raise NotFoundException("Agent 会话不存在")
    if is_running(row):
        raise BadRequestException("该会话正在运行中，请先停止运行再删除")
    get_agent_session_store().delete(session_id)
    await repo.delete(session_id)
    return ok({}, message="会话已删除")


@router.get(
    "/runs/{run_id}/stream",
    summary="订阅 Agent 运行事件（SSE，支持断线续传）",
)
async def stream_agent_run(
    run_id: str,
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """先回放游标后的历史，再实时推送新事件，直到运行进入终态。

    SSE ``id`` 是运行内从 1 开始的递增序号。客户端首次订阅不传
    ``Last-Event-ID`` 即可回放全部；重连时传最后已处理的 id，服务端只发送
    缺失事件。心跳使用 SSE 注释，不进入事件日志，也不推进游标。
    """
    registry = get_agent_run_registry()
    cursor = last_event_id or 0
    # 在 StreamingResponse 建立前完成存在性和游标校验，确保 404/400 仍能以
    # 标准 JSON 错误返回，而不是已经发出 200 后才在生成器里异常断流。
    initial_events, initial_terminal = await registry.get_events(
        run_id,
        cursor,
        timeout_seconds=0,
    )

    async def event_source():
        nonlocal cursor
        events = initial_events
        terminal = initial_terminal
        while True:
            for stored in events:
                cursor = stored.sequence
                event = stored.event
                yield (
                    f"id: {stored.sequence}\n"
                    f"event: {event.type}\n"
                    f"data: {event.model_dump_json(exclude_none=True)}\n\n"
                )
            if terminal:
                return
            events, terminal = await registry.get_events(
                run_id,
                cursor,
                timeout_seconds=15,
            )
            if not events and not terminal:
                yield ": heartbeat\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # SSE 反缓冲三件套（原理见 routes/search.py 的流式端点）
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/runs/{run_id}/cancel",
    response_model=ApiResponse[dict],
    summary="取消一次 Agent 运行",
)
async def cancel_agent_run(run_id: str) -> ApiResponse[dict]:
    """幂等请求取消后台任务；运行的 SSE 会以 agent_cancelled 事件收尾。"""
    await get_agent_run_registry().cancel(run_id)
    return ok({}, message="已请求停止 Agent 运行")
