"""豆瓣发现视角单元测试：只使用桩数据，不访问真实豆瓣。"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from movieclaw_media.douban import DoubanClient, DoubanDiscoverService, DoubanError
from movieclaw_media.models import MediaKind, MediaSource


def _item(idx: int, **overrides: Any) -> dict[str, Any]:
    item = {
        "id": str(idx),
        "title": f"豆瓣电影{idx}",
        "card_subtitle": "2026 / 中国大陆 / 剧情 科幻 / 某导演 / 某演员",
        "cover": {"url": f"https://img.example/{idx}.jpg"},
        "rating": {"value": 8.26},
        "description": f"简介{idx}",
    }
    item.update(overrides)
    return item


class StubDoubanClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    async def collection(self, collection_id: str, *, count: int = 30) -> dict[str, Any]:
        self.calls.append((collection_id, count))
        value = self.responses.get(collection_id, {"subject_collection_items": []})
        if isinstance(value, Exception):
            raise value
        return value

    async def detail(self, douban_id: str) -> dict[str, Any]:
        value = self.responses.get(f"detail:{douban_id}")
        if isinstance(value, Exception):
            raise value
        return value

    async def aclose(self) -> None:
        pass


async def test_movie_collection_maps_to_unified_cards() -> None:
    client = StubDoubanClient(
        {"movie_real_time_hotest": {"subject_collection_items": [_item(i) for i in range(1, 6)]}}
    )
    page = await DoubanDiscoverService(client).discover_page(MediaKind.MOVIE)  # type: ignore[arg-type]

    assert page.hero == []
    card = page.rows[0].items[0]
    assert card.source is MediaSource.DOUBAN
    assert card.id == "1"
    assert card.year == 2026
    assert card.rating == 8.3
    assert card.genres == ["剧情", "科幻"]


async def test_search_parses_lightweight_mobile_results() -> None:
    """搜索只映射页面真实提供的 ID、标题、评分和海报，并升级海报尺寸。"""
    html = """
    <ul class="search_results_subjects">
      <li><a href="/movie/subject/26266893/">
        <img src="https://img3.doubanio.com/view/photo/s_ratio_poster/public/a.jpg" />
        <span class="subject-title">流浪地球</span>
        <span class="rating-stars" data-rating="79.0"></span>
      </a></li>
      <li><a href="/movie/subject/36212391/">
        <img src="https://img9.doubanio.com/view/photo/s_ratio_poster/public/b.jpg" />
        <span class="subject-title">流浪地球3</span>
      </a></li>
    </ul>
    """

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["query"] == "流浪地球"
        assert request.url.params["type"] == "1002"
        return httpx.Response(200, text=html)

    client = DoubanClient(transport=httpx.MockTransport(handler))
    results = await client.search("流浪地球")
    await client.aclose()

    assert [item["id"] for item in results] == ["26266893", "36212391"]
    assert results[0]["rating"] == 7.9
    assert results[1]["rating"] == 0
    assert "/m_ratio_poster/" in results[0]["poster_url"]


async def test_detail_maps_mobile_fields_to_shared_detail_model() -> None:
    """豆瓣详情复用 MediaDetail，并补充别名与来源链接，缺少图片集时保持空列表。"""
    client = StubDoubanClient(
        {
            "detail:26266893": {
                "id": "26266893",
                "type": "movie",
                "title": "流浪地球",
                "original_title": "",
                "year": "2019",
                "rating": {"value": 7.9},
                "cover_url": "https://img3.doubanio.com/a.jpg",
                "intro": "太阳即将毁灭。",
                "genres": ["科幻", "冒险", "灾难"],
                "countries": ["中国大陆"],
                "languages": ["汉语普通话", "英语"],
                "durations": ["125分钟"],
                "pubdate": ["2019-02-05(中国大陆)"],
                "aka": ["The Wandering Earth", "流浪地球：飞跃2020特别版"],
                "directors": [{"name": "郭帆"}],
                "actors": [{"name": f"演员{i}"} for i in range(1, 8)],
                "url": "https://m.douban.com/movie/subject/26266893/",
            }
        }
    )
    detail = await DoubanDiscoverService(client).media_detail("26266893")  # type: ignore[arg-type]

    assert detail.card.title == "流浪地球"
    assert detail.card.original_title == "The Wandering Earth"
    assert detail.card.extent == "125分钟"
    assert detail.facts.directors == ["郭帆"]
    assert len(detail.facts.cast) == 5
    assert detail.facts.aliases[0] == "The Wandering Earth"
    assert detail.facts.source_url.endswith("/26266893/")
    assert detail.backdrops == []
    assert detail.related == []


async def test_collection_filters_wrong_media_type() -> None:
    items = [_item(i, type="tv") for i in range(1, 5)] + [_item(i) for i in range(5, 9)]
    client = StubDoubanClient(
        {"movie_real_time_hotest": {"subject_collection_items": items}}
    )
    page = await DoubanDiscoverService(client).discover_page(MediaKind.MOVIE)  # type: ignore[arg-type]
    assert [card.id for card in page.rows[0].items] == ["5", "6", "7", "8"]


async def test_single_collection_failure_is_isolated() -> None:
    client = StubDoubanClient(
        {
            "movie_real_time_hotest": DoubanError("模拟失败"),
            "movie_weekly_best": {"subject_collection_items": [_item(i) for i in range(1, 6)]},
        }
    )
    page = await DoubanDiscoverService(client).discover_page(MediaKind.MOVIE)  # type: ignore[arg-type]
    assert [row.id for row in page.rows] == ["douban-movie_weekly_best"]


async def test_top250_requests_and_returns_full_collection() -> None:
    """Top 250 不沿用普通榜单 30 条上限，应请求并保留全部 250 条。"""
    items = [_item(i) for i in range(1, 251)]
    client = StubDoubanClient(
        {"movie_top250": {"subject_collection_items": items}}
    )
    page = await DoubanDiscoverService(client).discover_page(MediaKind.MOVIE)  # type: ignore[arg-type]

    top250 = next(row for row in page.rows if row.id == "douban-movie_top250")
    assert len(top250.items) == 250
    assert ("movie_top250", 250) in client.calls
    assert ("movie_real_time_hotest", 30) in client.calls


async def test_all_collections_failure_raises_readable_error() -> None:
    client = StubDoubanClient(
        {
            key: DoubanError("豆瓣不可用")
            for key in (
                "movie_real_time_hotest",
                "movie_weekly_best",
                "movie_top250",
                "EC7Q5H2QI",
            )
        }
    )
    with pytest.raises(DoubanError, match="豆瓣不可用"):
        await DoubanDiscoverService(client).discover_page(MediaKind.MOVIE)  # type: ignore[arg-type]
