"""发现页接口的端到端测试：鉴权集成、成功链路、未配置 Key 的引导错误。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.auth import reset_auth_state
from movieclaw_api.services.media_discover import reset_media_service
from movieclaw_api.settings.store import reset_setting_store
from movieclaw_db.crypto import reset_secret_box
from movieclaw_media.models import (
    DiscoverPage,
    MediaCard,
    MediaDetail,
    MediaFacts,
    MediaKind,
    MediaRow,
    MediaSearchItem,
    MediaSource,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    # 显式置空以覆盖代码内置的默认 Key：用例需要「未配置」状态且不许出网
    monkeypatch.setenv("TMDB_API_KEY", "")
    get_settings.cache_clear()
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    reset_media_service()

    from movieclaw_api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        # 建号即自动登录，后续请求携带会话 Cookie
        c.post(
            "/api/v1/auth/bootstrap",
            json={"username": "admin", "password": "s3cret-pass"},
        )
        yield c

    reset_media_service()
    reset_setting_store()
    reset_secret_box()
    reset_auth_state()
    get_settings.cache_clear()


def _sample_page() -> DiscoverPage:
    card = MediaCard(
        id="42",
        type=MediaKind.MOVIE,
        title="示例电影",
        original_title="Sample",
        year=2026,
        rating=8.0,
        genres=["科幻"],
        overview="……",
        poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
        backdrop_url="https://image.tmdb.org/t/p/w1280/y.jpg",
    )
    return DiscoverPage(
        hero=[card],
        rows=[MediaRow(id="popular", title="热门电影", items=[card])],
    )


class _StubService:
    async def discover_page(self, kind: MediaKind) -> DiscoverPage:
        return _sample_page()

    async def search(self, keyword: str) -> list[MediaSearchItem]:
        return [
            MediaSearchItem(
                id="26266893",
                source=MediaSource.DOUBAN,
                title=keyword,
                rating=7.9,
                poster_url="https://img3.doubanio.com/a.jpg",
            )
        ]

    async def media_detail(self, douban_id: str):
        page = _sample_page()
        page.hero[0].id = douban_id
        return MediaDetail(
            card=page.hero[0],
            facts=MediaFacts(aliases=["示例别名"], source_url="https://m.douban.com/"),
        )


def test_discover_page_success(client: TestClient, monkeypatch) -> None:
    """成功链路：返回统一信封包装的页面数据，字段为 snake_case。"""
    from movieclaw_api.api.routes import discover as discover_route

    monkeypatch.setattr(discover_route, "get_media_service", lambda: _StubService())

    resp = client.get("/api/v1/discover/movie")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["hero"][0]["poster_url"].startswith("https://image.tmdb.org")
    assert data["rows"][0]["title"] == "热门电影"
    assert data["rows"][0]["ranked"] is False


def test_discover_douban_source_uses_independent_service(client: TestClient, monkeypatch) -> None:
    """豆瓣视角不依赖 TMDB Key，并路由到独立榜单服务。"""
    from movieclaw_api.api.routes import discover as discover_route

    monkeypatch.setattr(discover_route, "get_douban_media_service", lambda: _StubService())
    resp = client.get("/api/v1/discover/movie?source=douban")
    assert resp.status_code == 200
    assert resp.json()["data"]["rows"][0]["title"] == "热门电影"


def test_douban_search_returns_lightweight_results(client: TestClient, monkeypatch) -> None:
    """豆瓣搜索走独立静态路由，不会被 /{kind} 路由误判为非法媒体类型。"""
    from movieclaw_api.api.routes import discover as discover_route

    monkeypatch.setattr(discover_route, "get_douban_media_service", lambda: _StubService())
    resp = client.get("/api/v1/discover/search?source=douban&q=流浪地球")
    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["id"] == "26266893"
    assert item["title"] == "流浪地球"
    assert item["source"] == "douban"


def test_tmdb_search_returns_typed_results(client: TestClient, monkeypatch) -> None:
    """TMDB 搜索来源：走 TMDB 服务，条目带年份与 movie/tv 类型。"""
    from movieclaw_api.api.routes import discover as discover_route

    class _StubTmdbSearch:
        async def search(self, keyword: str) -> list[MediaSearchItem]:
            return [
                MediaSearchItem(
                    id="693134",
                    source=MediaSource.TMDB,
                    title=keyword,
                    year=2024,
                    type=MediaKind.MOVIE,
                    rating=8.2,
                    poster_url="https://image.tmdb.org/t/p/w342/d.jpg",
                )
            ]

    monkeypatch.setattr(discover_route, "get_media_service", lambda: _StubTmdbSearch())
    resp = client.get("/api/v1/discover/search?source=tmdb&q=沙丘")
    assert resp.status_code == 200
    item = resp.json()["data"][0]
    assert item["source"] == "tmdb"
    assert item["year"] == 2024
    assert item["type"] == "movie"


def test_tmdb_search_without_key_returns_guidance(client: TestClient) -> None:
    """TMDB 未配置 Key 时搜索报 502 + 中文配置引导（前端据此在区域内提示）。"""
    resp = client.get("/api/v1/discover/search?source=tmdb&q=沙丘")
    assert resp.status_code == 502
    assert "TMDB_API_KEY" in resp.json()["message"]


def test_douban_detail_uses_independent_route(client: TestClient, monkeypatch) -> None:
    from movieclaw_api.api.routes import discover as discover_route

    monkeypatch.setattr(discover_route, "get_douban_media_service", lambda: _StubService())
    resp = client.get("/api/v1/discover/douban/26266893")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["card"]["id"] == "26266893"
    assert data["facts"]["aliases"] == ["示例别名"]


def test_discover_rejects_unknown_kind(client: TestClient) -> None:
    """kind 只接受 movie / tv，其余按参数校验 422 拒绝。"""
    assert client.get("/api/v1/discover/book").status_code == 422


def test_discover_without_key_returns_guidance(client: TestClient) -> None:
    """TMDB_API_KEY 置空（禁用内置 Key）：502 + 中文引导信息。"""
    resp = client.get("/api/v1/discover/movie")
    assert resp.status_code == 502
    body = resp.json()
    assert body["success"] is False
    assert "TMDB_API_KEY" in body["message"]
