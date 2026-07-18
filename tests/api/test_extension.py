"""浏览器插件同步接口的端到端测试。

覆盖：令牌生成/回显/重置/撤销、插件侧接口的令牌鉴权、按域名推送 Cookie 后的
站点识别与状态流转、不支持 cookie 的站点与未知域名的错误处理。验证流程用"假验证"
替换，避免真实网络请求，使状态可确定性断言。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import movieclaw_api.api.routes.extension as ext_routes
from movieclaw_api.core.config import get_settings
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box
from movieclaw_db.engine import get_database
from movieclaw_db.models.site_credential import ConfigStatus
from movieclaw_db.repositories.credential_repo import CredentialRepository


async def _fake_verify_site(site_id: str) -> None:
    """假验证：一律判为成功（ACTIVE），聚焦于推送链路本身。"""
    async with get_database().session() as session:
        await CredentialRepository(session).update_status(site_id, ConfigStatus.ACTIVE)


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 每个测试用独立临时 SQLite 库与独立密钥文件，保证隔离
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    # 本套测试不涉及定时任务；关掉调度器，避免其进程内单例跨用例/事件循环残留，
    # 干扰后续基于 asyncio 的测试（如访问日志断言）。
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    # 配置存储/加密器是模块级单例，且 lifespan 关闭时不会重置。用例间必须手动重置，
    # 否则上个测试缓存的令牌会串到下个测试（不同临时库）。
    reset_setting_store()
    reset_secret_box()

    # 用假验证替换真实网络验证
    monkeypatch.setattr(ext_routes, "verify_site", _fake_verify_site)

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 令牌管理接口（Web 后台侧）需要管理员登录，这里用依赖覆盖绕过；
    # 插件侧接口的 sync token 鉴权不受影响，仍按真实逻辑测试。
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:  # with 块内触发 lifespan：建库、迁移、初始化配置内核
        yield c

    reset_setting_store()
    reset_secret_box()
    get_settings.cache_clear()


def _new_token(client: TestClient) -> str:
    return client.post("/api/v1/extension/token").json()["data"]["token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 令牌管理
# ---------------------------------------------------------------------------


def test_token_starts_disabled_then_generate_and_reveal(client: TestClient) -> None:
    # 初始未启用
    d = client.get("/api/v1/extension/token").json()["data"]
    assert d["enabled"] is False
    assert d["token"] is None

    # 生成后启用，且能回显供复制
    gen = client.post("/api/v1/extension/token").json()["data"]
    assert gen["enabled"] is True
    assert gen["token"]
    assert gen["created_at"]

    # 再次查看应回显同一个令牌
    again = client.get("/api/v1/extension/token").json()["data"]
    assert again["token"] == gen["token"]


def test_regenerate_invalidates_old_token(client: TestClient) -> None:
    t1 = _new_token(client)
    t2 = _new_token(client)
    assert t1 != t2
    # 旧令牌立即失效
    assert client.get("/api/v1/extension/ping", headers=_auth(t1)).status_code == 401
    # 新令牌可用
    assert client.get("/api/v1/extension/ping", headers=_auth(t2)).status_code == 200


def test_revoke_disables_sync(client: TestClient) -> None:
    token = _new_token(client)
    assert client.delete("/api/v1/extension/token").status_code == 200
    assert client.get("/api/v1/extension/token").json()["data"]["enabled"] is False
    # 撤销后原令牌不再有效
    assert client.get("/api/v1/extension/ping", headers=_auth(token)).status_code == 401


# ---------------------------------------------------------------------------
# 令牌鉴权
# ---------------------------------------------------------------------------


def test_ping_requires_token(client: TestClient) -> None:
    _new_token(client)
    # 不带令牌 → 401
    r = client.get("/api/v1/extension/ping")
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_ping_rejects_wrong_token(client: TestClient) -> None:
    _new_token(client)
    r = client.get("/api/v1/extension/ping", headers=_auth("obviously-wrong"))
    assert r.status_code == 401


def test_sync_disabled_rejects_even_with_any_token(client: TestClient) -> None:
    # 从未生成令牌时，任何请求都应被拒（提示先去后台生成）
    r = client.get("/api/v1/extension/ping", headers=_auth("anything"))
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 支持 Cookie 的站点列表
# ---------------------------------------------------------------------------


def test_list_cookie_sites_excludes_apikey_only(client: TestClient) -> None:
    token = _new_token(client)
    data = client.get("/api/v1/extension/sites", headers=_auth(token)).json()["data"]
    by_id = {x["site_id"]: x for x in data}
    # nexusphp 站点支持 cookie；M-Team 仅 API-Key，应被排除
    assert {"ttg", "chdbits", "ssd"} <= set(by_id)
    assert "mteam" not in by_id
    # 每个站点带匹配域名，供插件比对当前标签页
    assert by_id["ttg"]["domain"] == "totheglory.im"
    assert by_id["ttg"]["configured"] is False


# ---------------------------------------------------------------------------
# 推送 Cookie
# ---------------------------------------------------------------------------


def test_push_cookie_configures_and_verifies(client: TestClient) -> None:
    token = _new_token(client)
    r = client.post(
        "/api/v1/extension/cookies",
        headers=_auth(token),
        json={"domain": "totheglory.im", "cookie": "c_secure_uid=abc; c_secure_pass=xyz"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["site_id"] == "ttg"
    assert data["display_name"]
    # 推送后同步占位为 verifying
    assert data["status"] == "verifying"

    # 后台假验证跑完后站点转 active、可用（复用现有站点接口观察）
    d = client.get("/api/v1/sites/ttg").json()["data"]
    assert d["status"] == "active"
    assert d["usable"] is True

    # 插件侧站点列表也应反映"已配置且可用"
    sites = client.get("/api/v1/extension/sites", headers=_auth(token)).json()["data"]
    ttg = next(x for x in sites if x["site_id"] == "ttg")
    assert ttg["configured"] is True
    assert ttg["usable"] is True


def test_push_matches_by_registrable_domain(client: TestClient) -> None:
    """子域名也应归并到同一可注册域名并命中站点。"""
    token = _new_token(client)
    r = client.post(
        "/api/v1/extension/cookies",
        headers=_auth(token),
        json={"domain": "www.totheglory.im", "cookie": "c=1"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["site_id"] == "ttg"


def test_push_unknown_domain_returns_404(client: TestClient) -> None:
    token = _new_token(client)
    r = client.post(
        "/api/v1/extension/cookies",
        headers=_auth(token),
        json={"domain": "no-such-site.example", "cookie": "c=1"},
    )
    assert r.status_code == 404


def test_push_to_apikey_only_site_returns_400(client: TestClient) -> None:
    """域名能命中 M-Team，但它只支持 API-Key，cookie 推送应被拒（400）。"""
    token = _new_token(client)
    r = client.post(
        "/api/v1/extension/cookies",
        headers=_auth(token),
        json={"domain": "kp.m-team.cc", "cookie": "c=1"},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "BAD_REQUEST"


def test_push_requires_token(client: TestClient) -> None:
    _new_token(client)
    r = client.post(
        "/api/v1/extension/cookies",
        json={"domain": "totheglory.im", "cookie": "c=1"},
    )
    assert r.status_code == 401
