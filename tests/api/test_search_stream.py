"""SSE 流式搜索端点（GET /search/stream）的端到端测试。

覆盖：事件序列完整性（start → site_start × N → site_result/site_error × N → done）、
快站先于慢站出结果（流式的核心价值）、单站失败被隔离成 site_error、
以及与阻塞版一致的历史落库行为。站点访问同 test_search 用假实现替换。
"""
from __future__ import annotations

import asyncio
import json
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
    """假站点：可设 delay 模拟慢站，按预设返回结果或抛错。"""

    def __init__(
        self,
        items: list[TorrentListItem] | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ):
        self._items = items or []
        self._error = error
        self._delay = delay

    async def search(self, query: SearchQuery) -> SearchResult:
        if self._delay:
            await asyncio.sleep(self._delay)
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


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析成 (event, data) 列表，顺序即推送顺序。"""
    events: list[tuple[str, dict]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event, data = None, None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        assert event is not None and data is not None, f"SSE 块格式非法：{block!r}"
        events.append((event, data))
    return events


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


def test_stream_event_sequence(client: TestClient, monkeypatch) -> None:
    """事件序列完整：start（含站点清单）→ 每站 site_start → 每站结果 → done 汇总。"""
    _wire(
        monkeypatch,
        {
            "mteam": _FakeSite(items=[_item("m1", "沙丘"), _item("m2", "沙丘2")]),
            "ttg": _FakeSite(items=[_item("t1", "沙丘 REMUX")]),
        },
    )

    resp = client.get("/api/v1/search/stream", params={"keyword": "沙丘"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(resp.text)
    names = [e for e, _ in events]

    assert names[0] == "start"
    assert names.count("site_start") == 2
    assert names.count("site_result") == 2
    assert names[-1] == "done"

    start = events[0][1]
    assert start["keyword"] == "沙丘"
    assert {s["site_id"] for s in start["sites"]} == {"mteam", "ttg"}

    results = {d["site_id"]: d for e, d in events if e == "site_result"}
    assert results["mteam"]["count"] == 2
    assert [i["torrent_id"] for i in results["mteam"]["items"]] == ["m1", "m2"]
    # 每条结果带来源站点标识与扩充属性字段（与阻塞版 TorrentHit 同构）
    assert results["ttg"]["items"][0]["site_name"]
    assert "attrs" in results["ttg"]["items"][0]

    done = events[-1][1]
    assert done["total"] == 3
    assert {s["site_id"]: s["count"] for s in done["sites"]} == {"mteam": 2, "ttg": 1}


def test_stream_fast_site_arrives_before_slow(client: TestClient, monkeypatch) -> None:
    """快站先出结果——流式搜索的核心价值：不被最慢的站点拖住。"""
    _wire(
        monkeypatch,
        {
            "slow": _FakeSite(items=[_item("s1", "沙丘")], delay=0.2),
            "fast": _FakeSite(items=[_item("f1", "沙丘")]),
        },
    )

    events = _parse_sse(
        client.get("/api/v1/search/stream", params={"keyword": "沙丘"}).text
    )
    result_order = [d["site_id"] for e, d in events if e == "site_result"]
    assert result_order == ["fast", "slow"]
    # 慢站耗时被如实记录（≥ 人为延迟）
    slow = next(d for e, d in events if e == "site_result" and d["site_id"] == "slow")
    assert slow["elapsed_ms"] >= 200


def test_stream_isolates_single_site_failure(client: TestClient, monkeypatch) -> None:
    """单站失败只产生 site_error 事件，不中断流；done 汇总里记为 error。"""
    _wire(
        monkeypatch,
        {
            "mteam": _FakeSite(items=[_item("m1", "奥本海默")]),
            "ttg": _FakeSite(error=RuntimeError("boom")),
        },
    )

    events = _parse_sse(
        client.get("/api/v1/search/stream", params={"keyword": "奥本海默"}).text
    )
    names = [e for e, _ in events]
    assert names.count("site_result") == 1
    assert names.count("site_error") == 1
    assert names[-1] == "done"

    error = next(d for e, d in events if e == "site_error")
    assert error["site_id"] == "ttg"
    assert error["error"]  # 可读中文原因非空

    done = events[-1][1]
    assert done["total"] == 1
    statuses = {s["site_id"]: s for s in done["sites"]}
    assert statuses["ttg"]["error"] is not None
    assert statuses["mteam"]["error"] is None


def test_stream_no_active_sites_yields_empty_done(client: TestClient, monkeypatch) -> None:
    """没有可用站点时也走完整序列：start 站点清单为空，直接 done。"""
    _wire(monkeypatch, {})

    events = _parse_sse(
        client.get("/api/v1/search/stream", params={"keyword": "沙丘"}).text
    )
    assert [e for e, _ in events] == ["start", "done"]
    assert events[0][1]["sites"] == []
    assert events[-1][1]["total"] == 0


def test_stream_records_search_history(client: TestClient, monkeypatch) -> None:
    """流式搜索与阻塞版同口径落搜索历史（仅第 1 页）。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "沙丘")])})

    client.get("/api/v1/search/stream", params={"keyword": "沙丘", "label": "电影"})
    client.get("/api/v1/search/stream", params={"keyword": "沙丘", "label": "电影", "page": 2})

    history = client.get("/api/v1/search/history").json()["data"]
    assert len(history) == 1
    assert history[0]["keyword"] == "沙丘"
    assert history[0]["search_count"] == 1  # 第 2 页不重复计数


def test_stream_no_history_skips_recording(client: TestClient, monkeypatch) -> None:
    """无痕搜索（no_history=true）：搜索照常执行，但不写入搜索历史。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "沙丘")])})

    resp = client.get(
        "/api/v1/search/stream",
        params={"keyword": "沙丘", "label": "隐私分类", "no_history": "true"},
    )
    events = _parse_sse(resp.text)
    assert events[-1][0] == "done"  # 搜索本身正常完成

    assert client.get("/api/v1/search/history").json()["data"] == []
