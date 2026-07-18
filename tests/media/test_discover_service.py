"""movieclaw_media 发现页编排的单元测试（TMDB 客户端用桩替身，不出网）。"""

from __future__ import annotations

from typing import Any

import pytest

from movieclaw_media.models import MediaKind
from movieclaw_media.service import MediaDiscoverService
from movieclaw_media.tmdb import TmdbError

_IMAGE_BASE = "https://image.tmdb.org/t/p"

# 类型表响应（movie / tv 共用即可）
_GENRES = {"genres": [{"id": 878, "name": "科幻"}, {"id": 16, "name": "动画"}, {"id": 28, "name": "动作"}]}


def _movie(idx: int, **overrides: Any) -> dict[str, Any]:
    """构造一个字段齐全的 TMDB 电影列表条目。"""
    item = {
        "id": idx,
        "title": f"电影{idx}",
        "original_title": f"Movie {idx}",
        "release_date": "2026-01-15",
        "vote_average": 7.84,
        "genre_ids": [878, 28],
        "overview": f"简介{idx}",
        "poster_path": f"/p{idx}.jpg",
        "backdrop_path": f"/b{idx}.jpg",
    }
    item.update(overrides)
    return item


class StubTmdbClient:
    """按路径返回预置响应的桩客户端；值为异常时抛出，模拟单榜单失败。"""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, dict(params or {})))
        result = self.responses.get(path)
        if result is None:
            return {"results": []}
        if isinstance(result, Exception):
            raise result
        return result

    async def aclose(self) -> None:  # pragma: no cover - 接口兼容
        pass


def _service(responses: dict[str, Any]) -> MediaDiscoverService:
    return MediaDiscoverService(
        StubTmdbClient({"genre/movie/list": _GENRES, "genre/tv/list": _GENRES, **responses}),
        image_base_url=_IMAGE_BASE,
    )


# ---------------------------------------------------------------------------
# 条目映射
# ---------------------------------------------------------------------------


async def test_card_mapping_fields() -> None:
    """TMDB 原始条目 → MediaCard 的字段映射：图床拼接 / 年份截取 / 评分取整 / 类型翻译。"""
    svc = _service({"trending/movie/week": {"results": [_movie(1)]}})
    page = await svc.discover_page(MediaKind.MOVIE)

    card = page.hero[0]
    assert card.id == "1"
    assert card.title == "电影1"
    assert card.year == 2026
    assert card.rating == 7.8
    assert card.genres == ["科幻", "动作"]
    assert card.poster_url == f"{_IMAGE_BASE}/w500/p1.jpg"
    assert card.backdrop_url == f"{_IMAGE_BASE}/w1280/b1.jpg"
    assert card.extent == ""  # 列表接口拿不到片长，留空由详情回填


async def test_card_drops_items_missing_essentials() -> None:
    """缺海报 / 缺日期的条目没法上墙，应被剔除而非渲染残卡。"""
    rows = {
        "results": [
            _movie(1),
            _movie(2, poster_path=None),
            _movie(3, release_date=""),
            _movie(4),
            _movie(5),
            _movie(6),
        ]
    }
    svc = _service({"movie/popular": rows})
    page = await svc.discover_page(MediaKind.MOVIE)

    popular = next(r for r in page.rows if r.id == "popular")
    assert [c.id for c in popular.items] == ["1", "4", "5", "6"]


# ---------------------------------------------------------------------------
# 页面编排
# ---------------------------------------------------------------------------


async def test_hero_requires_backdrop_and_overview() -> None:
    """Hero 只收「有宽幅剧照且有简介」的条目，保证大横幅视觉完整。"""
    trending = {
        "results": [
            _movie(1, backdrop_path=None),
            _movie(2, overview=""),
            _movie(3),
        ]
    }
    svc = _service({"trending/movie/week": trending})
    page = await svc.discover_page(MediaKind.MOVIE)
    assert [c.id for c in page.hero] == ["3"]


async def test_ranked_row_limits_to_top10() -> None:
    """今日热榜行是 Top 10 排名行：ranked=True 且最多 10 条。"""
    trending_day = {"results": [_movie(i) for i in range(1, 15)]}
    svc = _service({"trending/movie/day": trending_day})
    page = await svc.discover_page(MediaKind.MOVIE)

    top = next(r for r in page.rows if r.id == "trending-day")
    assert top.ranked is True
    assert len(top.items) == 10


async def test_single_row_failure_is_isolated() -> None:
    """单个榜单失败只丢那一行，其余行照常返回（错误隔离口径与站点搜索一致）。"""
    svc = _service(
        {
            "movie/popular": {"results": [_movie(i) for i in range(1, 8)]},
            "movie/top_rated": TmdbError("模拟失败"),
        }
    )
    page = await svc.discover_page(MediaKind.MOVIE)

    row_ids = [r.id for r in page.rows]
    assert "popular" in row_ids
    assert "top-rated" not in row_ids


async def test_sparse_row_is_dropped() -> None:
    """条目太少的行（如所选地区暂无热映数据）整行隐藏，不渲染半空行。"""
    svc = _service(
        {
            "movie/now_playing": {"results": [_movie(1), _movie(2)]},
            "movie/popular": {"results": [_movie(i) for i in range(10, 16)]},
        }
    )
    page = await svc.discover_page(MediaKind.MOVIE)
    assert all(r.id != "now-playing" for r in page.rows)


async def test_total_failure_raises_first_error() -> None:
    """整页全军覆没时向上抛出第一个真实错误（而不是静默返回空页面）。"""
    boom = TmdbError("TMDB API Key 无效")
    responses = dict.fromkeys(
        [
            "trending/movie/week",
            "trending/movie/day",
            "movie/popular",
            "movie/now_playing",
            "movie/top_rated",
            "movie/upcoming",
            "discover/movie",
        ],
        boom,
    )
    svc = _service(responses)
    with pytest.raises(TmdbError, match="Key 无效"):
        await svc.discover_page(MediaKind.MOVIE)


async def test_page_is_cached() -> None:
    """发现页结果有 TTL 缓存：第二次请求不再回源 TMDB。"""
    svc = _service({"movie/popular": {"results": [_movie(i) for i in range(1, 8)]}})
    await svc.discover_page(MediaKind.MOVIE)
    client: StubTmdbClient = svc._client  # type: ignore[assignment]
    calls_after_first = len(client.calls)

    await svc.discover_page(MediaKind.MOVIE)
    assert len(client.calls) == calls_after_first


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------


async def test_search_maps_multi_results() -> None:
    """multi 搜索映射：人物条目和缺海报条目剔除；电影/剧集带年份与类型。"""
    svc = _service(
        {
            "search/multi": {
                "results": [
                    {"media_type": "person", "id": 1, "name": "某演员"},
                    _movie(2, media_type="movie"),
                    _movie(3, media_type="movie", poster_path=None),
                    {
                        "media_type": "tv",
                        "id": 4,
                        "name": "剧集4",
                        "first_air_date": "2024-04-01",
                        "vote_average": 8.44,
                        "poster_path": "/tv4.jpg",
                    },
                    {
                        "media_type": "tv",
                        "id": 5,
                        "name": "未定档剧集",
                        "first_air_date": "",
                        "poster_path": "/tv5.jpg",
                    },
                ]
            }
        }
    )
    items = await svc.search("测试")

    assert [i.id for i in items] == ["2", "4", "5"]
    movie = items[0]
    assert movie.source == "tmdb"
    assert movie.type == MediaKind.MOVIE
    assert movie.year == 2026
    assert movie.poster_url == f"{_IMAGE_BASE}/w342/p2.jpg"
    tv = items[1]
    assert tv.type == MediaKind.TV
    assert tv.rating == 8.4
    assert items[2].year is None  # 无首播日期不伪造年份


async def test_search_is_cached() -> None:
    """相同关键词（忽略首尾空白与大小写）十分钟内不重复回源。"""
    svc = _service({"search/multi": {"results": [_movie(1, media_type="movie")]}})
    await svc.search("Dune")
    client: StubTmdbClient = svc._client  # type: ignore[assignment]
    calls_after_first = len(client.calls)

    await svc.search(" dune ")
    assert len(client.calls) == calls_after_first


# ---------------------------------------------------------------------------
# 条目详情
# ---------------------------------------------------------------------------

_MOVIE_DETAIL = {
    **_movie(603),
    "genres": [{"id": 878, "name": "科幻"}],
    "genre_ids": None,
    "runtime": 136,
    "original_language": "en",
    "production_countries": [{"iso_3166_1": "US", "name": "United States of America"}],
    "credits": {
        "crew": [
            {"name": "莉莉·沃卓斯基", "job": "Director"},
            {"name": "某制片", "job": "Producer"},
        ],
        "cast": [{"name": f"演员{i}"} for i in range(8)],
    },
    "images": {
        "backdrops": [
            {"file_path": f"/bd{i}.jpg", "width": 3840, "height": 2160, "iso_639_1": None}
            for i in range(25)
        ],
        "posters": [
            {"file_path": "/p-en.jpg", "width": 2000, "height": 3000, "iso_639_1": "en"},
            {"file_path": "/p-null.jpg", "width": 2000, "height": 3000, "iso_639_1": None},
            {"file_path": "/p-zh.jpg", "width": 2000, "height": 3000, "iso_639_1": "zh"},
        ],
    },
    "recommendations": {"results": [_movie(i) for i in range(700, 715)]},
}


async def test_movie_detail_fields() -> None:
    svc = _service({"movie/603": _MOVIE_DETAIL})
    detail = await svc.media_detail(MediaKind.MOVIE, 603)

    assert detail.card.extent == "136 分钟"
    assert detail.facts.directors == ["莉莉·沃卓斯基"]
    assert len(detail.facts.cast) == 5
    assert detail.facts.country == "美国"
    assert detail.facts.language == "英语"
    assert detail.facts.network is None
    assert len(detail.related) == 10  # 相似推荐截断到 10 部


async def test_detail_images_mapping() -> None:
    """图片集映射：剧照截断到 20 张、预览/原图两档 URL；海报中文版排最前。"""
    svc = _service({"movie/603": _MOVIE_DETAIL})
    detail = await svc.media_detail(MediaKind.MOVIE, 603)

    assert len(detail.backdrops) == 20
    first = detail.backdrops[0]
    assert first.preview_url == f"{_IMAGE_BASE}/w780/bd0.jpg"
    assert first.full_url == f"{_IMAGE_BASE}/original/bd0.jpg"
    assert (first.width, first.height) == (3840, 2160)

    # 配置语言（zh）的海报置顶，其余保持 TMDB 原序
    assert [p.preview_url.rsplit("/", 1)[-1] for p in detail.posters] == [
        "p-zh.jpg",
        "p-en.jpg",
        "p-null.jpg",
    ]
    assert detail.posters[0].preview_url.startswith(f"{_IMAGE_BASE}/w342/")


async def test_tv_detail_fields() -> None:
    tv_detail = {
        "id": 1399,
        "name": "剧集X",
        "original_name": "Show X",
        "first_air_date": "2024-04-01",
        "vote_average": 8.4,
        "genres": [{"id": 16, "name": "动画"}],
        "poster_path": "/tv.jpg",
        "backdrop_path": "/tvb.jpg",
        "overview": "剧集简介",
        "number_of_seasons": 3,
        "original_language": "ja",
        "origin_country": ["JP"],
        "created_by": [{"name": "主创A"}],
        "networks": [{"name": "TV Tokyo"}],
        "credits": {"cast": [{"name": "声优1"}]},
        "recommendations": {"results": []},
    }
    svc = _service({"tv/1399": tv_detail})
    detail = await svc.media_detail(MediaKind.TV, 1399)

    assert detail.card.extent == "3 季"
    assert detail.card.year == 2024
    assert detail.facts.directors == ["主创A"]
    assert detail.facts.country == "日本"
    assert detail.facts.language == "日语"
    assert detail.facts.network == "TV Tokyo"
