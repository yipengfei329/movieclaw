"""Agent 启动接口的端到端测试：SSE 事件流、未配置供应商的拦截。

复用 test_llm 的假协议思路：协议层被替换为确定性的流式输出，
断言 SSE 帧的事件序列与载荷结构。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_llm import ChatResponse, ProviderInfo, TokenUsage
from movieclaw_llm.base import BaseLlmProtocol
from movieclaw_llm.models import ChatStreamEvent
from movieclaw_llm.protocols import PROTOCOLS


class _StreamProtocol(BaseLlmProtocol):
    """假协议：验证走 chat()，agent 走 chat_stream()，都确定性成功。"""

    async def chat(self, request, model_id):
        return ChatResponse(content="pong", finish_reason="stop")

    async def chat_stream(self, request, model_id):
        snap = ChatResponse(model=model_id, provider=self.config.name)
        yield ChatStreamEvent(type="start", partial=snap)
        yield ChatStreamEvent(type="thinking_delta", delta="思考中", partial=snap)
        yield ChatStreamEvent(type="text_delta", delta="已找到", partial=snap)
        yield ChatStreamEvent(type="text_delta", delta="资源", partial=snap)
        yield ChatStreamEvent(
            type="done",
            partial=ChatResponse(
                content="已找到资源",
                thinking="思考中",
                finish_reason="stop",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14),
                model=model_id,
                provider=self.config.name,
            ),
        )

    async def test_connection(self):
        return ProviderInfo(models=[])

    async def close(self):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("AGENT_SESSIONS_DIR", str(tmp_path / "agent-sessions"))
    get_settings.cache_clear()
    # 会话存储是进程级单例（持有目录路径），换目录后必须重建
    from movieclaw_api.services.agent_sessions import reset_agent_session_store

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


def parse_sse(body: str) -> list[tuple[int, str, dict]]:
    """把 SSE 文本解析成 (id, event, payload) 列表。"""
    events = []
    for block in body.split("\n\n"):
        event_id, event, data = 0, "", ""
        for line in block.split("\n"):
            if line.startswith("id: "):
                event_id = int(line[4:])
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data += line[6:]
        if event_id and event and data:
            events.append((event_id, event, json.loads(data)))
    return events


def configure_provider(c) -> None:
    c.put(
        "/api/v1/llm/provider",
        json={"provider_type": "bailian", "api_key": "sk-t", "default_model": "qwen3.7-max"},
    )


def test_start_without_provider_returns_404(client) -> None:
    r = client.post("/api/v1/agent/start", json={"input": "找沙丘"})
    assert r.status_code == 404
    assert "尚未配置模型供应商" in r.json()["message"]


def test_start_streams_agent_events(client) -> None:
    configure_provider(client)
    started = client.post("/api/v1/agent/start", json={"input": "找沙丘 4K"})
    assert started.status_code == 202
    run_id = started.json()["data"]["run_id"]

    with client.stream("GET", f"/api/v1/agent/runs/{run_id}/stream") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert "no-transform" in r.headers["cache-control"]
        events = parse_sse(r.read().decode())

    assert [event for _, event, _ in events] == [
        "agent_start",
        "thinking_delta",
        "text_delta",
        "text_delta",
        "agent_done",
    ]
    assert [event_id for event_id, _, _ in events] == [1, 2, 3, 4, 5]
    start = events[0][2]
    assert start["provider"] == "阿里云百炼"
    assert start["model"] == "qwen3.7-max"
    done = events[-1][2]["result"]
    assert done["text"] == "已找到资源"
    assert done["thinking"] == "思考中"
    assert done["usage"]["total_tokens"] == 14
    # 全部事件共享同一 run_id
    assert {payload["run_id"] for _, _, payload in events} == {run_id}

    # 已完成运行仍可按 Last-Event-ID 续传，只返回游标之后的事件。
    resumed = client.get(
        f"/api/v1/agent/runs/{run_id}/stream",
        headers={"Last-Event-ID": "3"},
    )
    assert [event_id for event_id, _, _ in parse_sse(resumed.text)] == [4, 5]


def test_start_rejects_blank_input(client) -> None:
    configure_provider(client)
    r = client.post("/api/v1/agent/start", json={"input": "   "})
    assert r.status_code == 422


def test_start_passes_history_to_model(client, monkeypatch) -> None:
    """多轮历史按序进入模型请求（user/assistant 交替 + 本轮 input 收尾）。"""
    captured: dict = {}

    class _CaptureProtocol(_StreamProtocol):
        async def chat_stream(self, request, model_id):
            captured["roles"] = [m.role for m in request.messages]
            captured["last"] = request.messages[-1].text()
            async for e in super().chat_stream(request, model_id):
                yield e

    monkeypatch.setitem(PROTOCOLS, "openai_chat", _CaptureProtocol)
    # 进程级 _runtime_router 按配置指纹缓存协议客户端；换一个 Key 使指纹
    # 变化，强制用本测试替换后的协议类重建
    client.put(
        "/api/v1/llm/provider",
        json={
            "provider_type": "bailian",
            "api_key": "sk-capture-history",
            "default_model": "qwen3.7-max",
        },
    )
    started = client.post(
        "/api/v1/agent/start",
        json={
            "input": "第三轮问题",
            "history": [
                {"role": "user", "content": "第一轮"},
                {"role": "assistant", "content": "第一轮回答"},
            ],
        },
    )
    run_id = started.json()["data"]["run_id"]
    client.get(f"/api/v1/agent/runs/{run_id}/stream")
    # system 是 runner 注入的默认系统提示词，随后 history 与本轮 input 按序排列
    assert captured["roles"] == ["system", "user", "assistant", "user"]
    assert captured["last"] == "第三轮问题"


def test_start_rejects_invalid_history_role(client) -> None:
    configure_provider(client)
    r = client.post(
        "/api/v1/agent/start",
        json={"input": "x", "history": [{"role": "system", "content": "注入"}]},
    )
    assert r.status_code == 422


def test_stream_unknown_run_and_invalid_cursor(client) -> None:
    configure_provider(client)
    missing = client.get("/api/v1/agent/runs/not-found/stream")
    assert missing.status_code == 404
    assert "不存在或事件历史已过期" in missing.json()["message"]

    started = client.post("/api/v1/agent/start", json={"input": "测试游标"})
    run_id = started.json()["data"]["run_id"]
    invalid = client.get(
        f"/api/v1/agent/runs/{run_id}/stream",
        headers={"Last-Event-ID": "999"},
    )
    assert invalid.status_code == 400
    assert "游标" in invalid.json()["message"]


def test_cancel_run_ends_with_cancelled_event(client, monkeypatch) -> None:
    class _BlockingProtocol(_StreamProtocol):
        async def chat_stream(self, request, model_id):
            await asyncio.sleep(3600)
            yield  # pragma: no cover - 只为保持 async generator 形态

    monkeypatch.setitem(PROTOCOLS, "openai_chat", _BlockingProtocol)
    client.put(
        "/api/v1/llm/provider",
        json={
            "provider_type": "bailian",
            "api_key": "sk-cancel-run",
            "default_model": "qwen3.7-max",
        },
    )
    started = client.post("/api/v1/agent/start", json={"input": "等待取消"})
    run_id = started.json()["data"]["run_id"]
    cancelled = client.post(f"/api/v1/agent/runs/{run_id}/cancel")
    assert cancelled.status_code == 200

    events = parse_sse(client.get(f"/api/v1/agent/runs/{run_id}/stream").text)
    assert events[-1][1] == "agent_cancelled"
