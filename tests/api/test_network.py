"""网络与代理设置接口测试：读写配置、校验、生效联动。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.auth import reset_auth_state
from movieclaw_api.services.media_discover import reset_media_service
from movieclaw_api.services.network_egress import reset_network_egress
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box
from movieclaw_net import EgressConfig, apply_egress_config, resolve_proxy_url


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    # 保证 env 模式的探测结果可控
    for name in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    reset_media_service()
    reset_network_egress()
    apply_egress_config(EgressConfig())

    from movieclaw_api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        c.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": "s3cret-pass"},
        )
        yield c

    reset_media_service()
    reset_network_egress()
    apply_egress_config(EgressConfig())
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    get_settings.cache_clear()


def test_get_config_returns_defaults_and_catalog(client):
    resp = client.get("/api/v1/network/config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 默认：跟随环境变量，TMDB 与图片回源走代理
    assert data["proxy_mode"] == "env"
    assert sorted(data["proxy_services"]) == ["image", "tmdb"]
    service_ids = [item["id"] for item in data["services"]]
    assert {"tmdb", "image", "douban", "llm"} <= set(service_ids)
    # 镜像默认值供前端 placeholder 展示
    assert data["mirror_defaults"]["tmdb_api_base_url"].startswith("http")


def test_save_manual_proxy_takes_effect_immediately(client):
    resp = client.put(
        "/api/v1/network/config",
        json={
            "proxy_mode": "manual",
            "proxy_url": "socks5://192.168.1.2:7891",
            "proxy_services": ["tmdb", "site:mteam"],
        },
    )
    assert resp.status_code == 200
    # 保存后无需重启：出口层路由立即按新配置决策
    assert resolve_proxy_url("tmdb") == "socks5://192.168.1.2:7891"
    assert resolve_proxy_url("site:mteam") == "socks5://192.168.1.2:7891"
    assert resolve_proxy_url("douban") is None
    # 重新读取还原一致（proxy_url 加密落库后仍可回显）
    data = client.get("/api/v1/network/config").json()["data"]
    assert data["proxy_mode"] == "manual"
    assert data["proxy_url"] == "socks5://192.168.1.2:7891"


def test_save_rejects_bad_proxy_scheme(client):
    resp = client.put(
        "/api/v1/network/config",
        json={"proxy_mode": "manual", "proxy_url": "ftp://1.2.3.4:21"},
    )
    assert resp.status_code == 400
    assert "协议不支持" in resp.json()["message"]


def test_save_manual_requires_proxy_url(client):
    resp = client.put(
        "/api/v1/network/config",
        json={"proxy_mode": "manual", "proxy_url": ""},
    )
    assert resp.status_code == 400


def test_save_rejects_bad_mirror_url(client):
    resp = client.put(
        "/api/v1/network/config",
        json={"proxy_mode": "off", "tmdb_api_base_url": "not-a-url"},
    )
    assert resp.status_code == 400


def test_test_endpoint_rejects_unknown_service(client):
    resp = client.post("/api/v1/network/test", json={"service": "nope"})
    assert resp.status_code == 400


def test_test_endpoint_llm_unconfigured(client):
    resp = client.post("/api/v1/network/test", json={"service": "llm"})
    assert resp.status_code == 400
    assert "尚未配置" in resp.json()["message"]


def test_requires_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key2"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    reset_network_egress()

    from movieclaw_api.app import create_app

    with TestClient(create_app()) as c:
        assert c.get("/api/v1/network/config").status_code == 401

    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    reset_network_egress()
    get_settings.cache_clear()
