"""媒体搜索历史（vertical=media）的端到端测试。

覆盖：/discover/search 的 history 开关（默认不记录、显式开启才落库）、
媒体历史带 vertical 标识与结果快照、媒体/资源同关键词互不去重、
media-snapshot 端点的成功链路与两个快照端点的垂直守卫（404）。
豆瓣服务用假实现替换，不出网。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_media.models import MediaSearchItem, MediaSource


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.api.routes import discover as discover_route
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测历史业务：登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    # 豆瓣搜索换成假实现：固定返回两条候选
    monkeypatch.setattr(discover_route, "get_douban_media_service", lambda: _StubDouban())
    with TestClient(app) as c:  # with 块内触发 lifespan：建库、迁移
        yield c
    get_settings.cache_clear()


class _StubDouban:
    async def search(self, keyword: str) -> list[MediaSearchItem]:
        return [
            MediaSearchItem(
                id="1",
                source=MediaSource.DOUBAN,
                title=f"{keyword} 2",
                rating=8.3,
                poster_url="https://img.douban/a.jpg",
            ),
            MediaSearchItem(
                id="2",
                source=MediaSource.DOUBAN,
                title=keyword,
                rating=7.9,
                poster_url="https://img.douban/b.jpg",
            ),
        ]


def _history(client: TestClient) -> list[dict]:
    resp = client.get("/api/v1/search/history")
    assert resp.status_code == 200
    return resp.json()["data"]


def _search_media(client: TestClient, q: str, history: bool = True):
    params = {"q": q, "source": "douban"}
    if history:
        params["history"] = "true"
    resp = client.get("/api/v1/discover/search", params=params)
    assert resp.status_code == 200
    return resp.json()["data"]


def test_media_search_records_history_with_snapshot(client: TestClient) -> None:
    _search_media(client, "沙丘")

    items = _history(client)
    assert len(items) == 1
    assert items[0]["keyword"] == "沙丘"
    assert items[0]["vertical"] == "media"
    # 媒体搜索没有分类/站点维度
    assert items[0]["categories"] == []
    assert items[0]["site_ids"] == []
    assert items[0]["has_snapshot"] is True


def test_media_search_without_flag_not_recorded(client: TestClient) -> None:
    """发现页工具栏等场景不传 history，不产生历史记录。"""
    _search_media(client, "沙丘", history=False)
    assert _history(client) == []


def test_media_snapshot_roundtrip(client: TestClient) -> None:
    _search_media(client, "沙丘")
    history_id = _history(client)[0]["id"]

    resp = client.get(f"/api/v1/search/history/{history_id}/media-snapshot")
    assert resp.status_code == 200
    snap = resp.json()["data"]
    assert snap["keyword"] == "沙丘"
    assert snap["total"] == 2
    assert snap["items"][0]["title"] == "沙丘 2"
    assert snap["items"][0]["poster_url"] == "https://img.douban/a.jpg"
    assert snap["snapshot_at"].endswith("+00:00")


def test_media_and_torrent_history_kept_apart(client: TestClient) -> None:
    """同一关键词分别搜媒体和资源是两条独立历史，重复媒体搜索只累加计数。"""
    _search_media(client, "沙丘")
    _search_media(client, "沙丘")
    # 资源搜索（测试环境无站点，结果为空，但历史照记）
    client.get("/api/v1/search", params={"keyword": "沙丘"})

    items = _history(client)
    assert len(items) == 2
    by_vertical = {i["vertical"]: i for i in items}
    assert by_vertical["media"]["search_count"] == 2
    assert by_vertical["torrent"]["search_count"] == 1


def test_snapshot_endpoints_guard_vertical(client: TestClient) -> None:
    """两个快照端点各守各的垂直：拿错端点读一律 404。"""
    _search_media(client, "沙丘")
    client.get("/api/v1/search", params={"keyword": "奥本海默"})
    items = {i["vertical"]: i for i in _history(client)}

    media_id = items["media"]["id"]
    torrent_id = items["torrent"]["id"]
    assert (
        client.get(f"/api/v1/search/history/{media_id}/snapshot").status_code == 404
    )
    assert (
        client.get(
            f"/api/v1/search/history/{torrent_id}/media-snapshot"
        ).status_code
        == 404
    )
