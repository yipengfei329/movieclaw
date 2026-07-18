"""搜索历史接口的端到端测试。

覆盖：搜索自动落库（含分类/站点组合快照）、同组合去重计数（组合顺序无关）、
label 随最近一次搜索刷新、翻页不重复记录、按最近时间倒序、单条删除（含 404）、
全量清空。测试环境无任何已配置站点，/search 会返回空结果，但不影响「发起过
一次搜索」这一事实被记录——这正是历史功能关心的。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file}")
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    # 本文件只测搜索历史业务，登录鉴权用依赖覆盖绕过（鉴权本身在 test_auth 覆盖）
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as c:  # with 块内触发 lifespan：建库、迁移、加载站点目录
        yield c
    get_settings.cache_clear()


def _history(client: TestClient, **params) -> list[dict]:
    resp = client.get("/api/v1/search/history", params=params)
    assert resp.status_code == 200
    return resp.json()["data"]


def test_search_records_history_with_scope_snapshot(client: TestClient) -> None:
    client.get(
        "/api/v1/search",
        params={
            "keyword": "沙丘",
            "categories": ["movie", "documentary"],
            "sites": ["mteam"],
            "label": "MT 影纪",
        },
    )

    items = _history(client)
    assert len(items) == 1
    assert items[0]["keyword"] == "沙丘"
    assert items[0]["label"] == "MT 影纪"
    # 快照已归一化（排序）
    assert items[0]["categories"] == ["documentary", "movie"]
    assert items[0]["site_ids"] == ["mteam"]
    assert items[0]["search_count"] == 1
    # 时间必须带 UTC 时区标记，前端才能正确换算本地时间
    assert items[0]["last_searched_at"].endswith("+00:00")


def test_same_scope_dedups_regardless_of_order(client: TestClient) -> None:
    """同关键词 + 同组合（顺序不同）视为同一条历史，只累加计数。"""
    client.get(
        "/api/v1/search",
        params={"keyword": "沙丘", "categories": ["movie", "tv"], "label": "A"},
    )
    client.get(
        "/api/v1/search",
        params={"keyword": "沙丘", "categories": ["tv", "movie"], "label": "B"},
    )

    items = _history(client)
    assert len(items) == 1
    assert items[0]["search_count"] == 2
    # label 随最近一次搜索刷新（预设改名后历史显示新名字）
    assert items[0]["label"] == "B"


def test_different_scope_kept_apart(client: TestClient) -> None:
    client.get("/api/v1/search", params={"keyword": "沙丘"})
    client.get("/api/v1/search", params={"keyword": "沙丘", "categories": ["movie"]})
    client.get(
        "/api/v1/search",
        params={"keyword": "沙丘", "categories": ["movie"], "sites": ["mteam"]},
    )

    items = _history(client)
    assert len(items) == 3


def test_unscoped_search_has_null_label_empty_lists(client: TestClient) -> None:
    """「全部」标签的搜索：label 为 null、组合为空列表。"""
    client.get("/api/v1/search", params={"keyword": "沙丘"})

    items = _history(client)
    assert items[0]["label"] is None
    assert items[0]["categories"] == []
    assert items[0]["site_ids"] == []


def test_pagination_not_recorded_again(client: TestClient) -> None:
    client.get("/api/v1/search", params={"keyword": "沙丘"})
    client.get("/api/v1/search", params={"keyword": "沙丘", "page": 2})

    items = _history(client)
    assert len(items) == 1
    assert items[0]["search_count"] == 1  # 翻页不算新搜索


def test_no_history_search_not_recorded(client: TestClient) -> None:
    """无痕搜索（no_history=true，来自开了「无痕」的自定义分类）不落历史。"""
    resp = client.get(
        "/api/v1/search", params={"keyword": "沙丘", "no_history": "true"}
    )
    assert resp.status_code == 200  # 搜索本身正常执行

    assert _history(client) == []

    # 同关键词的普通搜索照常记录，计数从 1 开始（无痕那次没有被计入）
    client.get("/api/v1/search", params={"keyword": "沙丘"})
    items = _history(client)
    assert len(items) == 1
    assert items[0]["search_count"] == 1


def test_history_ordered_by_recency(client: TestClient) -> None:
    client.get("/api/v1/search", params={"keyword": "沙丘"})
    client.get("/api/v1/search", params={"keyword": "奥本海默"})
    client.get("/api/v1/search", params={"keyword": "沙丘"})  # 再搜一次，应升到最前

    items = _history(client)
    assert [i["keyword"] for i in items] == ["沙丘", "奥本海默"]

    # limit 生效
    assert len(_history(client, limit=1)) == 1


def test_history_limit_counts_keyword_groups(client: TestClient) -> None:
    """同关键词的不同范围属于一组，limit=1 仍应返回该组的全部记录。"""
    client.get("/api/v1/search", params={"keyword": "奥本海默"})
    client.get("/api/v1/search", params={"keyword": "星际穿越"})
    client.get(
        "/api/v1/search",
        params={"keyword": "星际穿越", "categories": ["movie"], "label": "电影"},
    )
    client.get(
        "/api/v1/search",
        params={"keyword": "星际穿越", "categories": ["tv"], "label": "剧集"},
    )

    items = _history(client, limit=1)
    assert len(items) == 3
    assert {item["keyword"] for item in items} == {"星际穿越"}
    assert {item["label"] for item in items} == {None, "电影", "剧集"}


def test_delete_single_history(client: TestClient) -> None:
    client.get("/api/v1/search", params={"keyword": "沙丘"})
    history_id = _history(client)[0]["id"]

    assert client.delete(f"/api/v1/search/history/{history_id}").status_code == 200
    assert _history(client) == []
    # 已删除的再删 → 404
    assert client.delete(f"/api/v1/search/history/{history_id}").status_code == 404


def test_clear_all_history(client: TestClient) -> None:
    client.get("/api/v1/search", params={"keyword": "沙丘"})
    client.get("/api/v1/search", params={"keyword": "奥本海默"})

    assert client.delete("/api/v1/search/history").status_code == 200
    assert _history(client) == []
