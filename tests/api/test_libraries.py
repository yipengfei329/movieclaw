"""媒体库配置接口（/libraries）的端到端测试 + 入库路径推导单元测试。

覆盖：首启种子两个默认库、CRUD 与校验（绝对路径/重名/空根）、每 kind
默认库不变量（首个自动默认、切换默认、删除默认交接）、save_path 推导
与目录名清洗。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.library_config import derive_save_path, sanitize_folder_name
from movieclaw_db.models.library import Library


@pytest.fixture
def client(tmp_path, monkeypatch):
    # 每个测试用独立临时 SQLite 库；种子根目录也指向临时目录
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("LIBRARY_DEFAULT_ROOT", str(tmp_path / "library"))
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测媒体库业务，登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:  # with 块内触发 lifespan：迁移 + 首启种子
        yield c
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 首启种子
# ---------------------------------------------------------------------------


def test_seed_creates_two_default_libraries(client) -> None:
    rows = client.get("/api/v1/libraries").json()["data"]
    assert {r["name"] for r in rows} == {"电影库", "剧集库"}
    assert {r["kind"] for r in rows} == {"movie", "tv"}
    assert all(r["is_default"] for r in rows)
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["movie"]["primary_root"].endswith("/library/movies")
    assert by_kind["tv"]["primary_root"].endswith("/library/tv")


# ---------------------------------------------------------------------------
# CRUD 与默认库不变量
# ---------------------------------------------------------------------------


def test_second_library_not_default_until_set(client) -> None:
    r = client.post(
        "/api/v1/libraries",
        json={"name": "动漫库", "kind": "tv", "root_paths": ["/media/anime"]},
    )
    assert r.status_code == 200
    anime = r.json()["data"]
    assert anime["is_default"] is False  # 该 kind 已有默认（种子的剧集库）

    r = client.post(f"/api/v1/libraries/{anime['id']}/default")
    assert r.json()["data"]["is_default"] is True
    rows = client.get("/api/v1/libraries", params={"kind": "tv"}).json()["data"]
    defaults = [x for x in rows if x["is_default"]]
    assert [x["name"] for x in defaults] == ["动漫库"]  # 同 kind 只剩一个默认
    # 另一 kind 的默认不受影响
    movie_rows = client.get("/api/v1/libraries", params={"kind": "movie"}).json()["data"]
    assert movie_rows[0]["is_default"] is True


def test_validation_rejects_bad_inputs(client) -> None:
    # 相对路径拒绝
    r = client.post(
        "/api/v1/libraries",
        json={"name": "坏库", "kind": "movie", "root_paths": ["media/x"]},
    )
    assert r.status_code == 400
    assert "绝对路径" in r.json()["message"]
    # 空根路径拒绝
    r = client.post(
        "/api/v1/libraries", json={"name": "坏库", "kind": "movie", "root_paths": ["  "]}
    )
    assert r.status_code == 400
    # 重名拒绝
    r = client.post(
        "/api/v1/libraries",
        json={"name": "电影库", "kind": "movie", "root_paths": ["/media/m2"]},
    )
    assert r.status_code == 409


def test_update_name_and_paths(client) -> None:
    rows = client.get("/api/v1/libraries", params={"kind": "movie"}).json()["data"]
    movie_id = rows[0]["id"]
    r = client.put(
        f"/api/v1/libraries/{movie_id}",
        json={
            "name": "4K 电影库",
            "kind": "movie",
            "root_paths": ["/media/movies", "/mnt/disk2/movies"],
        },
    )
    data = r.json()["data"]
    assert data["name"] == "4K 电影库"
    assert data["primary_root"] == "/media/movies"  # 第一个为主根
    assert data["root_paths"] == ["/media/movies", "/mnt/disk2/movies"]


def test_delete_default_hands_over_within_kind(client) -> None:
    anime = client.post(
        "/api/v1/libraries",
        json={"name": "动漫库", "kind": "tv", "root_paths": ["/media/anime"]},
    ).json()["data"]
    tv_default = [
        x
        for x in client.get("/api/v1/libraries", params={"kind": "tv"}).json()["data"]
        if x["is_default"]
    ][0]
    assert client.delete(f"/api/v1/libraries/{tv_default['id']}").status_code == 200
    rows = client.get("/api/v1/libraries", params={"kind": "tv"}).json()["data"]
    assert [x["id"] for x in rows] == [anime["id"]]
    assert rows[0]["is_default"] is True  # 默认交接给同 kind 剩下的库


# ---------------------------------------------------------------------------
# save_path 推导（纯函数单元测试）
# ---------------------------------------------------------------------------


def _lib(paths: list[str]) -> Library:
    return Library(name="库", kind="movie", root_paths=paths)


def test_derive_save_path_normal_and_no_year() -> None:
    lib = _lib(["/media/movies/"])
    assert derive_save_path(lib, title="沙丘", year=2021) == "/media/movies/沙丘 (2021)"
    assert derive_save_path(lib, title="沙丘", year=None) == "/media/movies/沙丘"


def test_derive_save_path_sanitizes_title() -> None:
    lib = _lib(["/media/movies"])
    assert (
        derive_save_path(lib, title="Mission: Impossible / Fallout", year=2018)
        == "/media/movies/Mission Impossible Fallout (2018)"
    )


def test_derive_save_path_without_roots_returns_none() -> None:
    assert derive_save_path(_lib([]), title="沙丘", year=2021) is None


def test_sanitize_folder_name_edge_cases() -> None:
    assert sanitize_folder_name('a<b>:c"d/e\\f|g?h*i') == "a b c d e f g h i"
    assert sanitize_folder_name("  结尾点. ") == "结尾点"
    assert sanitize_folder_name("///") == "未命名"
