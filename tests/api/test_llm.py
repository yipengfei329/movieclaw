"""LLM 供应商配置接口的端到端测试。

覆盖：单例 upsert 语义、保存后异步验证的状态流转、API Key 脱敏与落库
加密、预设列表、base_url 必填校验。真实协议实现被替换为假协议，
不发真实请求，使状态流转可确定性断言。
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_llm import ChatResponse, LlmConnectError, ProviderInfo
from movieclaw_llm.base import BaseLlmProtocol
from movieclaw_llm.models import LlmProviderConfig
from movieclaw_llm.protocols import PROTOCOLS

# 假协议行为开关：key 含 "bad" 时模拟连不上；含 "nolist" 时模拟无 /models 接口
_BAD_KEY_MARK = "bad"
_NO_LIST_MARK = "nolist"

# 每次验证收到的领域配置，供断言"传给协议层的 key 已解密"
_captured_configs: list[LlmProviderConfig] = []


class _FakeProtocol(BaseLlmProtocol):
    """假协议：跳过真实网络，按 api_key 决定测试结果。"""

    async def chat(self, request, model_id):
        _captured_configs.append(self.config)
        if _BAD_KEY_MARK in self.config.api_key:
            raise LlmConnectError("连接模型服务失败，请检查网络与 base_url 配置")
        return ChatResponse(content="pong", finish_reason="stop")

    async def chat_stream(self, request, model_id):  # pragma: no cover
        yield None

    async def test_connection(self) -> ProviderInfo:
        if _NO_LIST_MARK in self.config.api_key:
            raise LlmConnectError("该端点不提供模型列表接口")
        return ProviderInfo(models=["qwen-plus", "qwen-max"])

    async def close(self) -> None:
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 每个测试用独立临时 SQLite 库与密钥文件，保证隔离
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    get_settings.cache_clear()

    # 用假协议替换 openai_chat 协议实现
    _captured_configs.clear()
    monkeypatch.setitem(PROTOCOLS, "openai_chat", _FakeProtocol)

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测 LLM 配置业务，登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:  # with 块内触发 lifespan：建库、迁移、初始化加密器
        yield c, db_file
    get_settings.cache_clear()


_PAYLOAD = {
    "provider_type": "bailian",
    "api_key": "sk-live-123456",
    "default_model": "qwen3.7-max",
}


def test_get_before_configured_returns_null(client) -> None:
    c, _ = client
    r = c.get("/api/v1/llm/provider")
    assert r.status_code == 200
    assert r.json()["data"] is None


def test_save_then_async_verify_active_and_desensitized(client) -> None:
    c, _ = client
    r = c.put("/api/v1/llm/provider", json=_PAYLOAD)
    assert r.status_code == 200
    data = r.json()["data"]
    # 接口立即返回 verifying（同步占位），绝不回传 API Key
    assert data["status"] == "verifying"
    assert "api_key" not in data

    # TestClient 的 BackgroundTasks 在响应后同步执行完毕 → 再查已是终态
    detail = c.get("/api/v1/llm/provider").json()["data"]
    assert detail["status"] == "active"
    assert detail["usable"] is True
    assert detail["last_error"] is None
    assert detail["last_checked_at"] is not None
    assert detail["available_models"] == ["qwen-max", "qwen-plus"]

    # 传给协议层的 key 是解密后的明文（证明加密→解密链路正确）
    assert _captured_configs[-1].api_key == "sk-live-123456"
    # base_url 留空 → 领域配置也留空（由预设提供百炼默认端点）
    assert _captured_configs[-1].base_url is None
    assert _captured_configs[-1].default_model == "qwen3.7-max"


def test_api_key_encrypted_at_rest(client) -> None:
    c, db_file = client
    c.put("/api/v1/llm/provider", json=_PAYLOAD)

    # 直接读 SQLite 文件核实落库形态：密文带 enc:: 前缀，不含明文
    row = sqlite3.connect(db_file).execute("SELECT api_key FROM llm_provider").fetchone()
    assert row[0].startswith("enc::")
    assert "sk-live-123456" not in row[0]


def test_bad_key_marked_failed_with_chinese_error(client) -> None:
    c, _ = client
    c.put("/api/v1/llm/provider", json={**_PAYLOAD, "api_key": "sk-bad-key"})
    detail = c.get("/api/v1/llm/provider").json()["data"]
    assert detail["status"] == "failed"
    assert detail["usable"] is False
    assert "连接模型服务失败" in detail["last_error"]


def test_model_list_failure_does_not_affect_verdict(client) -> None:
    c, _ = client
    c.put("/api/v1/llm/provider", json={**_PAYLOAD, "api_key": "sk-nolist-key"})
    detail = c.get("/api/v1/llm/provider").json()["data"]
    # 对话验证通过即 active；模型列表拉不到只是没有提示数据
    assert detail["status"] == "active"
    assert detail["available_models"] is None


# 自定义端点的完整请求体：default_model 带齐参数配置
_COMPAT_PAYLOAD = {
    "provider_type": "openai_compat",
    "base_url": "http://192.168.1.5:8000/v1",
    "api_key": "sk-vllm",
    "default_model": "my-local-model",
    "extra_models": [
        {
            "id": "my-local-model",
            "context_window": 131072,
            "max_output_tokens": 8192,
            "supports_tools": True,
        }
    ],
}


def test_upsert_keeps_single_row(client) -> None:
    c, db_file = client
    c.put("/api/v1/llm/provider", json=_PAYLOAD)
    c.put("/api/v1/llm/provider", json=_COMPAT_PAYLOAD)
    rows = sqlite3.connect(db_file).execute("SELECT COUNT(*) FROM llm_provider").fetchone()
    assert rows[0] == 1
    detail = c.get("/api/v1/llm/provider").json()["data"]
    assert detail["provider_type"] == "openai_compat"
    assert detail["base_url"] == "http://192.168.1.5:8000/v1"
    # 自定义模型目录随配置持久化，参数完整回传（设置页下拉框的数据源）
    assert detail["extra_models"][0]["id"] == "my-local-model"
    assert detail["extra_models"][0]["context_window"] == 131072


def test_cataloged_provider_rejects_model_outside_catalog(client) -> None:
    """严格规则：官方渠道只认预设目录，自定义模型即使带全参数也不行。"""
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={
            **_PAYLOAD,
            "default_model": "my-private-qwen",
            "extra_models": [
                {"id": "my-private-qwen", "context_window": 131072, "max_output_tokens": 8192}
            ],
        },
    )
    assert r.status_code == 400
    assert "不在「阿里云百炼」的模型目录中" in r.json()["message"]


def test_borrowed_catalog_model_exempt_from_manual_param_rules(client) -> None:
    """兼容端点借用目录模型（如 kimi 共享窗口、无独立输出上限）→ 豁免手填规则，可保存。"""
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={
            **_COMPAT_PAYLOAD,
            "default_model": "kimi-k2.5",
            "extra_models": [
                {
                    "id": "kimi-k2.5",
                    "context_window": 262144,
                    "supports_thinking": True,
                    "max_thinking_tokens": 81920,
                }
            ],
        },
    )
    assert r.status_code == 200
    detail = c.get("/api/v1/llm/provider").json()["data"]
    assert detail["status"] == "active"
    assert detail["default_model"] == "kimi-k2.5"


def test_custom_model_without_metadata_rejected(client) -> None:
    """自定义端点只给裸模型 id、不带参数 → 400，提示先补全参数。"""
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={**_COMPAT_PAYLOAD, "extra_models": []},
    )
    assert r.status_code == 400
    assert "补全它的参数配置" in r.json()["message"]


def test_custom_model_missing_required_params_rejected(client) -> None:
    """自定义模型缺上下文/最大输出 → 400。"""
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={
            **_COMPAT_PAYLOAD,
            "extra_models": [{"id": "my-local-model", "context_window": 131072}],
        },
    )
    assert r.status_code == 400
    assert "上下文长度与最大输出为必填" in r.json()["message"]


def test_custom_thinking_model_requires_budget(client) -> None:
    """自定义模型开思考但没填思考预算 → 400。"""
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={
            **_COMPAT_PAYLOAD,
            "extra_models": [
                {
                    "id": "my-local-model",
                    "context_window": 131072,
                    "max_output_tokens": 8192,
                    "supports_thinking": True,
                }
            ],
        },
    )
    assert r.status_code == 400
    assert "思考预算上限" in r.json()["message"]


def test_openai_compat_requires_base_url(client) -> None:
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={"provider_type": "openai_compat", "api_key": "k", "default_model": "m"},
    )
    assert r.status_code == 400
    assert "必须填写 API 端点地址" in r.json()["message"]


def test_unknown_provider_type_rejected(client) -> None:
    c, _ = client
    r = c.put(
        "/api/v1/llm/provider",
        json={**_PAYLOAD, "provider_type": "gemini"},
    )
    assert r.status_code == 400
    assert "未知的供应商类型" in r.json()["message"]


def test_reverify_without_config_404(client) -> None:
    c, _ = client
    r = c.post("/api/v1/llm/provider/verify")
    assert r.status_code == 404


def test_delete_then_get_null(client) -> None:
    c, _ = client
    c.put("/api/v1/llm/provider", json=_PAYLOAD)
    r = c.delete("/api/v1/llm/provider")
    assert r.status_code == 200
    assert c.get("/api/v1/llm/provider").json()["data"] is None


def test_presets_endpoint(client) -> None:
    c, _ = client
    presets = {p["id"]: p for p in c.get("/api/v1/llm/presets").json()["data"]}
    assert {"openai", "bailian", "openai_compat"} <= set(presets)
    assert presets["bailian"]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert presets["openai_compat"]["requires_base_url"] is True
    assert any(m["id"] == "qwen3.7-max" for m in presets["bailian"]["models"])
