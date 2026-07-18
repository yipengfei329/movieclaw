"""Agent 会话持久化：JSONL 存储、记录器收尾、索引重建与会话 API。

覆盖设计定案的四条关键约束：
1. append-only 线性链（uuid/parent_uuid 正确串联，坏行不毁会话）；
2. 只落定稿消息（assistant 带 model/usage 元数据，原样可回喂）；
3. 中断收尾补配对（tool_call 永远有回执）；
4. 索引可由文件整体重建（文件是事实源）。
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from movieclaw_agent.events import AgentEvent
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.agent_sessions import (
    AgentSessionStore,
    reset_agent_session_store,
)
from movieclaw_llm import ChatMessage, ChatResponse, TokenUsage, ToolCall
from movieclaw_llm.protocols import PROTOCOLS

from tests.api.test_agent import _StreamProtocol, configure_provider, parse_sse

# ---------------------------------------------------------------------------
# 存储层单元测试（纯文件，不依赖 DB / 应用）
# ---------------------------------------------------------------------------


def _assistant_with_tools() -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(id="call_1", name="read", arguments={"path": "a.txt"}),
            ToolCall(id="call_2", name="bash", arguments={"command": "ls"}),
        ],
    )


def test_store_roundtrip_linear_chain(tmp_path) -> None:
    store = AgentSessionStore(tmp_path)
    header = store.create()
    sid = header.session_id

    e1 = store.append(sid, ChatMessage(role="user", content="找沙丘 4K"))
    e2 = store.append(
        sid,
        ChatMessage(role="assistant", content="好的，找到了"),
        model="kimi-k2",
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        finish_reason="stop",
    )

    header_read, entries = store.read(sid)
    assert header_read.session_id == sid
    assert header_read.version == 1
    assert [e.uuid for e in entries] == [e1.uuid, e2.uuid]
    # 线性链：首条 parent 为空，之后逐条回指
    assert entries[0].parent_uuid is None
    assert entries[1].parent_uuid == e1.uuid
    # assistant 信封元数据完整保留
    assert entries[1].model == "kimi-k2"
    assert entries[1].usage.total_tokens == 15
    assert entries[1].finish_reason == "stop"
    # 重建的 LLM 上下文就是原样消息
    history = store.build_history(sid)
    assert [m.role for m in history] == ["user", "assistant"]
    assert history[1].text() == "好的，找到了"


def test_store_skips_corrupt_tail_and_chains_after_reload(tmp_path) -> None:
    store = AgentSessionStore(tmp_path)
    sid = store.create().session_id
    good = store.append(sid, ChatMessage(role="user", content="第一条"))
    # 模拟进程崩溃留下的半行
    with store.path(sid).open("a", encoding="utf-8") as f:
        f.write('{"type":"message","uuid":"trunc')

    # 新进程（新 store 实例，缓存为空）读取：坏行被跳过
    fresh = AgentSessionStore(tmp_path)
    _, entries = fresh.read(sid)
    assert [e.uuid for e in entries] == [good.uuid]
    # 继续追加：链尾接在最后一条合法 entry 上，而不是坏行
    e2 = fresh.append(sid, ChatMessage(role="user", content="第二条"))
    assert e2.parent_uuid == good.uuid


def test_seal_pending_tool_calls_completes_pairing(tmp_path) -> None:
    store = AgentSessionStore(tmp_path)
    sid = store.create().session_id
    store.append(sid, ChatMessage(role="user", content="执行任务"))
    store.append(sid, _assistant_with_tools())
    # 只有 call_1 有回执，call_2 因中断没有
    store.append(
        sid,
        ChatMessage(role="tool", content="文件内容", tool_call_id="call_1", name="read"),
    )

    assert store.seal_pending_tool_calls(sid) == 1
    _, entries = store.read(sid)
    sealed = entries[-1]
    assert sealed.message.role == "tool"
    assert sealed.message.tool_call_id == "call_2"
    assert "中断" in sealed.message.text()
    # 幂等：全部配对后再收尾不产生新行
    assert store.seal_pending_tool_calls(sid) == 0


def test_summarize_and_scan_all(tmp_path) -> None:
    store = AgentSessionStore(tmp_path)
    sid = store.create().session_id
    store.append(sid, ChatMessage(role="user", content="标题" * 100))
    store.append(sid, ChatMessage(role="assistant", content="答复"))
    store.append(sid, ChatMessage(role="user", content="第二轮提问"))

    summary = store.summarize(sid)
    assert summary.entry_count == 3
    assert len(summary.title) == 80  # 截断
    assert summary.last_prompt == "第二轮提问"
    assert summary.leaf_uuid == store.read(sid)[1][-1].uuid

    # 损坏文件（空文件）不拖垮全目录扫描
    (tmp_path / "broken.jsonl").write_text("", encoding="utf-8")
    summaries = store.scan_all()
    assert [s.session_id for s in summaries] == [sid]


# ---------------------------------------------------------------------------
# 记录器 + 索引（异步，真实 SQLite）
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    from movieclaw_db.engine import dispose_db, get_database, init_db
    from movieclaw_db.migrations import run_migrations

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'agent.db'}")
    monkeypatch.setenv("AGENT_SESSIONS_DIR", str(tmp_path / "agent-sessions"))
    get_settings.cache_clear()
    reset_agent_session_store()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()
    reset_agent_session_store()


async def test_recorder_lifecycle_and_terminal_sealing(db) -> None:
    """完整运行生命周期：落用户输入 → 定稿回调 → 取消收尾 → 状态清空。"""
    from movieclaw_api.services.agent_session_recorder import AgentSessionRecorder
    from movieclaw_api.services.agent_sessions import get_agent_session_store
    from movieclaw_db.repositories.agent_session_repo import (
        AgentSessionRepository,
        is_running,
    )

    store = get_agent_session_store()
    sid = store.create().session_id
    async with db.session() as session:
        await AgentSessionRepository(session).create(sid, title=None)

    recorder = AgentSessionRecorder(store, sid, entry_count=0)
    await recorder.begin("run123")
    await recorder.record_user_input("帮我找资源")
    await recorder.on_message(
        _assistant_with_tools(),
        ChatResponse(model="kimi-k2", finish_reason="tool_calls"),
    )
    # 模拟只执行完第一个工具就被取消
    await recorder.on_message(
        ChatMessage(role="tool", content="ok", tool_call_id="call_1", name="read"), None
    )

    async with db.session() as session:
        row = await AgentSessionRepository(session).get(sid)
        assert row.active_run_id == "run123"
        assert is_running(row)
        assert row.title == "帮我找资源"
        assert row.entry_count == 3

    await recorder.on_terminal(AgentEvent(type="agent_cancelled", run_id="run123"))

    _, entries = store.read(sid)
    # user + assistant + tool(call_1) + 补配对的 tool(call_2)
    assert [e.message.role for e in entries] == ["user", "assistant", "tool", "tool"]
    assert entries[-1].message.tool_call_id == "call_2"
    async with db.session() as session:
        row = await AgentSessionRepository(session).get(sid)
        assert row.active_run_id is None
        assert not is_running(row)
        assert row.entry_count == 4
        assert row.leaf_uuid == entries[-1].uuid


async def test_terminal_before_begin_leaves_session_not_running(db) -> None:
    """极快的运行可能在 begin 落库前就进入终态（后台任务先于编排层调度）。

    回归保护：此时 begin 必须跳过 mark_running 与心跳，否则会话被重新标成
    「进行中」，且孤儿心跳任务不断续期，状态永远无法自愈（曾致偶发失败）。
    """
    from movieclaw_api.services.agent_session_recorder import AgentSessionRecorder
    from movieclaw_api.services.agent_sessions import get_agent_session_store
    from movieclaw_db.repositories.agent_session_repo import (
        AgentSessionRepository,
        is_running,
    )

    store = get_agent_session_store()
    sid = store.create().session_id
    async with db.session() as session:
        await AgentSessionRepository(session).create(sid, title=None)

    recorder = AgentSessionRecorder(store, sid, entry_count=0)
    await recorder.on_terminal(AgentEvent(type="agent_done", run_id="fast"))
    await recorder.begin("fast")

    async with db.session() as session:
        row = await AgentSessionRepository(session).get(sid)
        assert row.active_run_id is None
        assert not is_running(row)
    # 心跳任务不应被创建（无人取消它会永远续期运行状态）
    assert recorder._heartbeat_task is None


async def test_rebuild_restores_index_from_files(db) -> None:
    """索引丢行/多行都能从文件校准回来（文件是事实源）。"""
    from movieclaw_api.services.agent_session_recorder import rebuild_agent_session_index
    from movieclaw_api.services.agent_sessions import get_agent_session_store
    from movieclaw_db.models import AgentSession
    from movieclaw_db.repositories.agent_session_repo import AgentSessionRepository

    store = get_agent_session_store()
    sid = store.create().session_id
    store.append(sid, ChatMessage(role="user", content="重建我"))
    # 场景 1：文件有、索引没有（崩在两步写入之间）
    # 场景 2：索引有、文件没有（用户手删了转录）
    async with db.session() as session:
        session.add(AgentSession(id="ghost", title="幽灵会话"))
        await session.commit()

    await rebuild_agent_session_index()

    async with db.session() as session:
        repo = AgentSessionRepository(session)
        restored = await repo.get(sid)
        assert restored is not None
        assert restored.title == "重建我"
        assert restored.entry_count == 1
        assert await repo.get("ghost") is None


# ---------------------------------------------------------------------------
# 会话 API 端到端（TestClient + 假协议）
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("AGENT_SESSIONS_DIR", str(tmp_path / "agent-sessions"))
    get_settings.cache_clear()
    reset_agent_session_store()
    monkeypatch.setitem(PROTOCOLS, "openai_chat", _StreamProtocol)

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()
    reset_agent_session_store()


def _run_turn(client, payload: dict) -> tuple[str, str]:
    """发起一轮运行并等待终态，返回 (session_id, run_id)。"""
    started = client.post("/api/v1/agent/start", json=payload)
    assert started.status_code == 202
    data = started.json()["data"]
    with client.stream("GET", f"/api/v1/agent/runs/{data['run_id']}/stream") as r:
        r.read()
    return data["session_id"], data["run_id"]


def _wait_not_running(client, session_id: str) -> dict:
    """等待终态收尾落库（on_terminal 与 SSE 收流并发，留一个短轮询窗）。"""
    for _ in range(50):
        item = client.get(f"/api/v1/agent/sessions/{session_id}").json()["data"]["session"]
        if not item["running"]:
            return item
        time.sleep(0.1)
    pytest.fail("会话运行状态未在期限内清空")


def test_start_creates_session_and_persists_turn(client) -> None:
    configure_provider(client)
    session_id, _ = _run_turn(client, {"input": "找沙丘 4K"})

    item = _wait_not_running(client, session_id)
    assert item["title"] == "找沙丘 4K"
    assert item["last_prompt"] == "找沙丘 4K"
    assert item["entry_count"] == 2  # user + 终答 assistant
    assert item["active_run_id"] is None

    detail = client.get(f"/api/v1/agent/sessions/{session_id}").json()["data"]
    roles = [e["message"]["role"] for e in detail["entries"]]
    assert roles == ["user", "assistant"]
    assistant = detail["entries"][1]
    # 定稿 assistant 带模型元数据；thinking 以内容块形式原样保留
    assert assistant["finish_reason"] == "stop"
    assert assistant["usage"]["total_tokens"] == 14
    parts = assistant["message"]["content"]
    assert [p["type"] for p in parts] == ["thinking", "text"]

    listing = client.get("/api/v1/agent/sessions").json()["data"]
    assert [s["id"] for s in listing] == [session_id]


def test_multi_turn_resume_builds_history_from_transcript(client, monkeypatch) -> None:
    """续聊的上下文来自服务端转录，而非前端回传。"""
    captured: dict = {}

    class _CaptureProtocol(_StreamProtocol):
        async def chat_stream(self, request, model_id):
            captured["roles"] = [m.role for m in request.messages]
            captured["last"] = request.messages[-1].text()
            async for e in super().chat_stream(request, model_id):
                yield e

    monkeypatch.setitem(PROTOCOLS, "openai_chat", _CaptureProtocol)
    # 进程级 _runtime_router 按配置指纹缓存协议客户端；换一个 Key 使指纹
    # 变化，强制用本测试替换后的协议类重建（同 test_agent 的既有做法）
    client.put(
        "/api/v1/llm/provider",
        json={
            "provider_type": "bailian",
            "api_key": "sk-session-resume",
            "default_model": "qwen3.7-max",
        },
    )

    session_id, _ = _run_turn(client, {"input": "第一轮"})
    _wait_not_running(client, session_id)
    # 故意带上与服务端不符的 history：session_id 存在时应被忽略
    second, _ = _run_turn(
        client,
        {
            "input": "第二轮",
            "session_id": session_id,
            "history": [{"role": "user", "content": "伪造历史"}],
        },
    )
    assert second == session_id
    assert captured["roles"] == ["system", "user", "assistant", "user"]
    assert captured["last"] == "第二轮"
    assert "伪造历史" not in str(captured)

    item = _wait_not_running(client, session_id)
    assert item["entry_count"] == 4
    assert item["last_prompt"] == "第二轮"
    assert item["title"] == "第一轮"  # 标题保持首轮


def test_start_on_unknown_session_returns_404(client) -> None:
    configure_provider(client)
    r = client.post("/api/v1/agent/start", json={"input": "x", "session_id": "missing"})
    assert r.status_code == 404


def test_rename_session_updates_index_title(client) -> None:
    configure_provider(client)
    session_id, _ = _run_turn(client, {"input": "起个名字"})
    _wait_not_running(client, session_id)

    r = client.patch(
        f"/api/v1/agent/sessions/{session_id}", json={"title": "  我的追剧计划  "}
    )
    assert r.status_code == 200
    assert r.json()["data"]["title"] == "我的追剧计划"
    # 列表同步生效；转录文件不因改名而变化（标题只是索引元数据）
    items = client.get("/api/v1/agent/sessions").json()["data"]
    assert items[0]["title"] == "我的追剧计划"

    assert client.patch(
        "/api/v1/agent/sessions/missing", json={"title": "x"}
    ).status_code == 404
    assert client.patch(
        f"/api/v1/agent/sessions/{session_id}", json={"title": "   "}
    ).status_code == 422


def test_delete_session_removes_file_and_index(client) -> None:
    from movieclaw_api.services.agent_sessions import get_agent_session_store

    configure_provider(client)
    session_id, _ = _run_turn(client, {"input": "删掉我"})
    _wait_not_running(client, session_id)

    assert client.delete(f"/api/v1/agent/sessions/{session_id}").status_code == 200
    assert client.get(f"/api/v1/agent/sessions/{session_id}").status_code == 404
    assert not get_agent_session_store().path(session_id).exists()
    assert client.get("/api/v1/agent/sessions").json()["data"] == []
