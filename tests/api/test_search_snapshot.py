"""搜索结果快照的端到端测试。

覆盖：流式/阻塞搜索完成后自动落快照、历史项的 has_snapshot 标记、
快照端点回读（items/sites/total/snapshot_at）、重搜覆盖旧快照、
无痕搜索不产生快照、不存在的历史报 404。
站点访问同 test_search 用假实现替换。
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

import movieclaw_api.services.site_search as site_search
from movieclaw_api.core.config import get_settings
from movieclaw_tracker.models import SearchQuery, SearchResult, TorrentListItem


@dataclass
class _Cred:
    site_id: str


class _FakeSite:
    def __init__(self, items: list[TorrentListItem] | None = None, error: Exception | None = None):
        self._items = items or []
        self._error = error

    async def search(self, query: SearchQuery) -> SearchResult:
        if self._error is not None:
            raise self._error
        return SearchResult(items=self._items, page=query.page, total_pages=1)


class _FakeManager:
    def __init__(self, sites: dict[str, _FakeSite]):
        self._sites = sites

    async def get(self, site_id: str) -> _FakeSite:
        return self._sites[site_id]


def _item(torrent_id: str, title: str) -> TorrentListItem:
    return TorrentListItem(torrent_id=torrent_id, title=title, seeders=10)


async def _async(value):
    return value


def _wire(monkeypatch, sites: dict[str, _FakeSite]) -> None:
    monkeypatch.setattr(
        site_search, "_active_sites", lambda: _async([_Cred(sid) for sid in sites])
    )
    monkeypatch.setattr(site_search, "get_site_access", lambda: _FakeManager(sites))


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _latest_history(client: TestClient) -> dict:
    return client.get("/api/v1/search/history").json()["data"][0]


def test_stream_search_saves_snapshot(client: TestClient, monkeypatch) -> None:
    """流式搜索走完后自动落快照：历史标记 has_snapshot，快照端点可回读完整结果。"""
    _wire(
        monkeypatch,
        {
            "mteam": _FakeSite(items=[_item("m1", "沙丘"), _item("m2", "沙丘2")]),
            "ttg": _FakeSite(error=RuntimeError("boom")),
        },
    )

    client.get("/api/v1/search/stream", params={"keyword": "沙丘", "label": "电影"})

    history = _latest_history(client)
    assert history["has_snapshot"] is True

    snap = client.get(f"/api/v1/search/history/{history['id']}/snapshot").json()["data"]
    assert snap["history_id"] == history["id"]
    assert snap["keyword"] == "沙丘"
    assert snap["label"] == "电影"
    assert snap["total"] == 2
    assert {i["torrent_id"] for i in snap["items"]} == {"m1", "m2"}
    # 逐站状态一并留存：失败站的可读原因与耗时也在快照里（超时诊断的关键）
    statuses = {s["site_id"]: s for s in snap["sites"]}
    assert statuses["mteam"]["count"] == 2
    assert statuses["mteam"]["elapsed_ms"] is not None
    assert statuses["ttg"]["error"] is not None
    assert statuses["ttg"]["elapsed_ms"] is not None
    # 整体耗时也留存，快照回放时页头/站点弹层可显示
    assert snap["elapsed_ms"] is not None
    # 快照时间是带时区的 ISO 串（前端直接给 lib/time.ts 换算相对时间）
    assert snap["snapshot_at"].endswith("+00:00")


def test_blocking_search_saves_snapshot(client: TestClient, monkeypatch) -> None:
    """阻塞版 /search 与流式版同口径落快照。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "奥本海默")])})

    client.get("/api/v1/search", params={"keyword": "奥本海默"})

    history = _latest_history(client)
    assert history["has_snapshot"] is True
    snap = client.get(f"/api/v1/search/history/{history['id']}/snapshot").json()["data"]
    assert snap["total"] == 1


def test_research_overwrites_snapshot(client: TestClient, monkeypatch) -> None:
    """同一组合重搜：历史行不新增，快照被最新结果覆盖。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("old1", "沙丘")])})
    client.get("/api/v1/search/stream", params={"keyword": "沙丘"})

    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("new1", "沙丘"), _item("new2", "沙丘2")])})
    client.get("/api/v1/search/stream", params={"keyword": "沙丘"})

    rows = client.get("/api/v1/search/history").json()["data"]
    assert len(rows) == 1  # 去重：同关键词同组合仍是一行
    snap = client.get(f"/api/v1/search/history/{rows[0]['id']}/snapshot").json()["data"]
    assert {i["torrent_id"] for i in snap["items"]} == {"new1", "new2"}


def test_no_history_search_saves_nothing(client: TestClient, monkeypatch) -> None:
    """无痕搜索不落历史，自然也没有快照。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "沙丘")])})

    client.get("/api/v1/search/stream", params={"keyword": "沙丘", "no_history": "true"})
    assert client.get("/api/v1/search/history").json()["data"] == []


def test_poster_mode_persisted_in_history(client: TestClient, monkeypatch) -> None:
    """图览模式偏好随历史留存：搜索时带 poster_mode=true，历史项即回传 true。

    修复的 bug：图览分类搜索后点历史看快照，展示模式被硬编码回列表——
    根因是历史没记录 poster_mode。现在它作为「怎么展示」的偏好随历史留存。
    """
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "沙丘")])})

    client.get(
        "/api/v1/search/stream",
        params={"keyword": "沙丘", "label": "4K 图览", "poster_mode": "true"},
    )
    history = _latest_history(client)
    assert history["poster_mode"] is True

    # 不带该参数（列表模式）默认 false
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "奥本海默")])})
    client.get("/api/v1/search/stream", params={"keyword": "奥本海默"})
    assert _latest_history(client)["poster_mode"] is False


def test_poster_mode_refreshed_on_research(client: TestClient, monkeypatch) -> None:
    """poster_mode 不进去重键：同组合换展示模式重搜，仍是一条历史、值刷新为最新。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "沙丘")])})
    client.get("/api/v1/search/stream", params={"keyword": "沙丘", "poster_mode": "true"})
    client.get("/api/v1/search/stream", params={"keyword": "沙丘"})  # 列表模式重搜

    rows = client.get("/api/v1/search/history").json()["data"]
    assert len(rows) == 1
    assert rows[0]["poster_mode"] is False  # 刷新为最近一次的偏好


def test_snapshot_of_missing_history_returns_404(client: TestClient) -> None:
    resp = client.get("/api/v1/search/history/9999/snapshot")
    assert resp.status_code == 404
