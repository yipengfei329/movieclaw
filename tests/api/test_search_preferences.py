"""搜索偏好（标签栏：内置分类 + 自定义分类）接口的端到端测试。

覆盖：默认值（常用四类可见、成人默认隐藏）、混排保存与持久化、预设的
校验规则（重名 / 空名 / 未知站点 / 数量上限）、缺失内置分类自动补齐、
预设内分类去重。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    # 配置存储/加密器是模块级单例，用例间必须手动重置，避免缓存串库
    reset_setting_store()
    reset_secret_box()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测偏好业务，登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:  # with 块内触发 lifespan：建库、迁移、初始化配置内核
        yield c

    reset_setting_store()
    reset_secret_box()
    get_settings.cache_clear()


def _get_tabs(client: TestClient) -> list[dict]:
    resp = client.get("/api/v1/search/preferences")
    assert resp.status_code == 200
    return resp.json()["data"]["tabs"]


def _preset(name: str = "热门影剧", **overrides) -> dict:
    base = {
        "type": "preset",
        "id": f"p-{name}",
        "name": name,
        "visible": True,
        "categories": ["movie", "tv"],
        "site_ids": [],
        "poster_mode": False,
        "skip_history": False,
    }
    return {**base, **overrides}


def test_default_tabs(client: TestClient) -> None:
    """从未配置时返回默认：常用四类可见且排前，成人等隐藏，无预设。"""
    tabs = _get_tabs(client)
    assert all(t["type"] == "category" for t in tabs)
    assert [t["id"] for t in tabs[:4]] == ["movie", "tv", "documentary", "anime"]
    assert all(t["visible"] for t in tabs[:4])
    hidden = {t["id"]: t["visible"] for t in tabs[4:]}
    assert hidden == {"music": False, "game": False, "av": False, "other": False}


def test_save_mixed_tabs_persists(client: TestClient) -> None:
    """预设插到内置分类中间保存，再次读取应原样返回（混排顺序保留）。"""
    tabs = _get_tabs(client)
    preset = _preset("MT 影剧", site_ids=["mteam"], poster_mode=True, skip_history=True)
    mixed = [tabs[0], preset, *tabs[1:]]

    resp = client.put("/api/v1/search/preferences", json={"tabs": mixed})
    assert resp.status_code == 200
    saved = resp.json()["data"]["tabs"]
    assert saved[1]["type"] == "preset"
    assert saved[1]["name"] == "MT 影剧"
    assert saved[1]["categories"] == ["movie", "tv"]
    assert saved[1]["site_ids"] == ["mteam"]
    assert saved[1]["poster_mode"] is True
    assert saved[1]["skip_history"] is True

    # 再次 GET：与保存结果一致（真正落库，而非只在内存）
    assert _get_tabs(client) == saved


def test_preset_optional_flags_default_off(client: TestClient) -> None:
    """不传 poster_mode / skip_history 的旧数据/旧前端：默认都关闭。"""
    tabs = _get_tabs(client)
    preset = _preset("无开关")
    del preset["poster_mode"]
    del preset["skip_history"]
    resp = client.put("/api/v1/search/preferences", json={"tabs": [*tabs, preset]})
    assert resp.status_code == 200
    saved = resp.json()["data"]["tabs"][-1]
    assert saved["poster_mode"] is False
    assert saved["skip_history"] is False


def test_preset_empty_scope_means_unlimited(client: TestClient) -> None:
    """分类/站点都为空的预设合法：语义为「不限分类 × 全部站点」。"""
    tabs = _get_tabs(client)
    resp = client.put(
        "/api/v1/search/preferences",
        json={"tabs": [*tabs, _preset("全站", categories=[], site_ids=[])]},
    )
    assert resp.status_code == 200
    saved = resp.json()["data"]["tabs"][-1]
    assert saved["categories"] == [] and saved["site_ids"] == []


def test_preset_categories_deduped(client: TestClient) -> None:
    """预设内重复勾选的分类静默去重（保序）。"""
    tabs = _get_tabs(client)
    resp = client.put(
        "/api/v1/search/preferences",
        json={"tabs": [*tabs, _preset("去重", categories=["tv", "movie", "tv"])]},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["tabs"][-1]["categories"] == ["tv", "movie"]


def test_duplicate_preset_name_rejected(client: TestClient) -> None:
    tabs = _get_tabs(client)
    resp = client.put(
        "/api/v1/search/preferences",
        json={"tabs": [*tabs, _preset("同名", id="p1"), _preset("同名", id="p2")]},
    )
    assert resp.status_code == 400
    assert "同名" in resp.json()["message"]


def test_blank_preset_name_rejected(client: TestClient) -> None:
    tabs = _get_tabs(client)
    resp = client.put(
        "/api/v1/search/preferences",
        json={"tabs": [*tabs, _preset("  ")]},
    )
    assert resp.status_code == 422


def test_unknown_site_rejected(client: TestClient) -> None:
    tabs = _get_tabs(client)
    resp = client.put(
        "/api/v1/search/preferences",
        json={"tabs": [*tabs, _preset("坏站点", site_ids=["not-a-site"])]},
    )
    assert resp.status_code == 400
    assert "not-a-site" in resp.json()["message"]


def test_preset_count_capped(client: TestClient) -> None:
    tabs = _get_tabs(client)
    many = [_preset(f"预设{i}", id=f"p{i}") for i in range(21)]
    resp = client.put("/api/v1/search/preferences", json={"tabs": [*tabs, *many]})
    assert resp.status_code == 400


def test_duplicate_builtin_category_rejected(client: TestClient) -> None:
    tabs = _get_tabs(client)
    resp = client.put(
        "/api/v1/search/preferences", json={"tabs": [*tabs, tabs[0]]}
    )
    assert resp.status_code == 400


def test_missing_builtin_backfilled(client: TestClient) -> None:
    """只传部分内置分类时，缺失项按默认可见性补到末尾；预设不受影响。"""
    resp = client.put(
        "/api/v1/search/preferences",
        json={
            "tabs": [
                {"type": "category", "id": "av", "visible": True},
                _preset("MT", site_ids=["mteam"]),
            ]
        },
    )
    assert resp.status_code == 200
    saved = resp.json()["data"]["tabs"]
    # av + 预设 + 补齐的 7 个内置分类
    assert len(saved) == 9
    assert saved[0] == {"type": "category", "id": "av", "visible": True}
    assert saved[1]["type"] == "preset"
    categories = [t["id"] for t in saved if t["type"] == "category"]
    assert len(categories) == 8
    assert next(t for t in saved if t.get("id") == "movie")["visible"] is True
