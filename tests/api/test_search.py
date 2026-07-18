"""跨站点聚合搜索接口的端到端测试。

覆盖：多站结果合并、单站失败被隔离成 error、分类过滤参数透传、关键词必填校验。
站点访问（活跃站点列表 + 站点实例）被替换为「假实现」，不触库、不发真实网络请求，
使断言可确定。「只搜已启用且验证通过的站点」这条判据与种子同步共用同一实现
（_active_sites），此处不重复覆盖，只覆盖搜索本身的合并与隔离逻辑。
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
    """_active_sites 返回值的最小替身——搜索链路只用到 site_id。"""

    site_id: str


class _FakeSite:
    """假站点：记录收到的 SearchQuery，按预设返回结果或抛错。"""

    def __init__(self, items: list[TorrentListItem] | None = None, error: Exception | None = None):
        self._items = items or []
        self._error = error
        self.last_query: SearchQuery | None = None

    async def search(self, query: SearchQuery) -> SearchResult:
        self.last_query = query
        if self._error is not None:
            raise self._error
        return SearchResult(items=self._items, page=query.page, total_pages=1)


class _FakeManager:
    """假站点访问管理器：按 site_id 返回预设的 _FakeSite。"""

    def __init__(self, sites: dict[str, _FakeSite]):
        self._sites = sites

    async def get(self, site_id: str) -> _FakeSite:
        return self._sites[site_id]


def _item(torrent_id: str, title: str) -> TorrentListItem:
    return TorrentListItem(torrent_id=torrent_id, title=title, seeders=10)


def _wire(monkeypatch, sites: dict[str, _FakeSite]) -> None:
    """把「活跃站点」与「站点实例来源」都替换成假实现。"""
    monkeypatch.setattr(
        site_search, "_active_sites", lambda: _async([_Cred(sid) for sid in sites])
    )
    monkeypatch.setattr(site_search, "get_site_access", lambda: _FakeManager(sites))


async def _async(value):
    return value


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测搜索业务，登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:  # with 块内触发 lifespan：建库、迁移、加载站点目录
        yield c
    get_settings.cache_clear()


def test_search_merges_results_across_sites(client: TestClient, monkeypatch) -> None:
    _wire(
        monkeypatch,
        {
            "mteam": _FakeSite(items=[_item("m1", "沙丘"), _item("m2", "沙丘2")]),
            "ttg": _FakeSite(items=[_item("t1", "沙丘 REMUX")]),
        },
    )

    data = client.get("/api/v1/search", params={"keyword": "沙丘"}).json()["data"]

    assert data["total"] == 3
    assert {i["site_id"] for i in data["items"]} == {"mteam", "ttg"}
    # 每条结果都带上了来源站点展示名
    assert all(i["site_name"] for i in data["items"])
    assert {s["site_id"]: s["count"] for s in data["sites"]} == {"mteam": 2, "ttg": 1}


def test_search_isolates_single_site_failure(client: TestClient, monkeypatch) -> None:
    _wire(
        monkeypatch,
        {
            "mteam": _FakeSite(items=[_item("m1", "奥本海默")]),
            "ttg": _FakeSite(error=RuntimeError("boom")),  # 该站崩溃
        },
    )

    data = client.get("/api/v1/search", params={"keyword": "奥本海默"}).json()["data"]

    # 失败站点不拖垮整体：正常站仍有结果，失败站记 error
    assert data["total"] == 1
    statuses = {s["site_id"]: s for s in data["sites"]}
    assert statuses["mteam"]["error"] is None
    assert statuses["ttg"]["error"] is not None
    assert statuses["ttg"]["count"] == 0


def test_search_passes_multi_category_filter(client: TestClient, monkeypatch) -> None:
    fake_site = _FakeSite(items=[_item("m1", "老友记")])
    _wire(monkeypatch, {"mteam": fake_site})

    resp = client.get(
        "/api/v1/search",
        params={"keyword": "老友记", "categories": ["tv", "documentary"], "label": "剧集"},
    )
    assert resp.status_code == 200
    # 分类组合原样透传给站点的 SearchQuery（tracker 层原生支持多分类）
    assert fake_site.last_query is not None
    assert [c.value for c in fake_site.last_query.categories] == ["tv", "documentary"]
    assert resp.json()["data"]["categories"] == ["tv", "documentary"]
    assert resp.json()["data"]["label"] == "剧集"


def test_search_filters_site_subset(client: TestClient, monkeypatch) -> None:
    """sites 参数圈定站点子集：只搜勾选的站点，未勾选的不出现在逐站状态里。"""
    mteam, ttg = _FakeSite(items=[_item("m1", "沙丘")]), _FakeSite(items=[_item("t1", "沙丘")])
    _wire(monkeypatch, {"mteam": mteam, "ttg": ttg})

    data = client.get(
        "/api/v1/search", params={"keyword": "沙丘", "sites": ["mteam"]}
    ).json()["data"]

    assert [s["site_id"] for s in data["sites"]] == ["mteam"]
    assert data["total"] == 1
    assert ttg.last_query is None  # 未勾选的站点根本没被请求


def test_search_unknown_site_subset_yields_empty(client: TestClient, monkeypatch) -> None:
    """勾选的站点全部不可用/不存在时返回空结果，而非报错（与逐站失败隔离口径一致）。"""
    _wire(monkeypatch, {"mteam": _FakeSite(items=[_item("m1", "沙丘")])})

    data = client.get(
        "/api/v1/search", params={"keyword": "沙丘", "sites": ["ttg"]}
    ).json()["data"]
    assert data["total"] == 0
    assert data["sites"] == []


def test_search_requires_keyword(client: TestClient) -> None:
    # 缺 keyword → 422；空 keyword 也被 min_length 拦下
    assert client.get("/api/v1/search").status_code == 422
    assert client.get("/api/v1/search", params={"keyword": ""}).status_code == 422


def test_search_rejects_invalid_category(client: TestClient) -> None:
    r = client.get("/api/v1/search", params={"keyword": "x", "categories": "不存在"})
    assert r.status_code == 422


def test_search_items_carry_enriched_attrs(client: TestClient, monkeypatch) -> None:
    """搜索结果的每条种子都带数据扩充层产出的结构化属性（端到端）。"""
    rich_item = TorrentListItem(
        torrent_id="d1",
        title="Dune.Part.Two.2024.2160p.UHD.BluRay.REMUX.HEVC.DV.HDR10.Atmos-CHD",
        subtitle="沙丘2：预言实现 | 国语中字",
    )
    _wire(monkeypatch, {"mteam": _FakeSite(items=[rich_item])})

    data = client.get("/api/v1/search", params={"keyword": "dune"}).json()["data"]

    attrs = data["items"][0]["attrs"]
    assert attrs["year"] == 2024
    assert attrs["resolution"] == "2160p"
    assert attrs["media_source"] == "UHD Blu-ray"
    assert attrs["remux"] is True
    assert attrs["video_codec"] == "HEVC"
    assert set(attrs["hdr"]) == {"DV", "HDR10"}
    assert "Atmos" in attrs["audio"]
    assert attrs["release_group"] == "CHD"
    # H&R 未配置选择器时保持三态里的"未知"，不误报成"无考核"
    assert data["items"][0]["hit_and_run"] is None
