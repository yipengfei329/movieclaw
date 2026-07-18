"""媒体身份层纯函数的单元测试：档案拉取、别名构建、豆瓣收敛三分支（MockTransport，不出网）。"""

from __future__ import annotations

import httpx

from movieclaw_media.library import (
    ResolveStatus,
    fetch_media_profile,
    resolve_douban_to_tmdb,
)
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"


def _client(routes: dict[str, dict], captured: list[httpx.Request] | None = None) -> TmdbClient:
    """按 URL path 路由返回固定 JSON 的假 TMDB。未注册的 path 一律 404。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        payload = routes.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=payload)

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


_MOVIE_DETAIL = {
    "id": 693134,
    "title": "沙丘2",
    "original_title": "Dune: Part Two",
    "release_date": "2024-02-27",
    "status": "Released",
    "poster_path": "/poster.jpg",
    "backdrop_path": "/backdrop.jpg",
    "external_ids": {"imdb_id": "tt15239678"},
    "alternative_titles": {
        "titles": [
            {"iso_3166_1": "CN", "title": "沙丘：第二部"},
            {"iso_3166_1": "US", "title": "Dune Part 2"},
            {"iso_3166_1": "FR", "title": "Dune Deuxième Partie"},  # 不在收集范围
            {"iso_3166_1": "HK", "title": "沙丘瀚战：第二章"},
        ]
    },
    "translations": {
        "translations": [
            {"iso_639_1": "zh", "data": {"title": "沙丘2"}},  # 与主标题重复，应去重
            {"iso_639_1": "en", "data": {"title": "Dune: Part Two"}},  # 与原名重复
            {"iso_639_1": "ja", "data": {"title": "デューン 砂の惑星PART2"}},  # 不收集
        ]
    },
}


async def test_fetch_movie_profile_fields_and_aliases() -> None:
    """电影档案：字段齐全；别名=主标题+原名+指定地区/语言，跨来源精确去重。"""
    client = _client({"/3/movie/693134": _MOVIE_DETAIL})
    profile = await fetch_media_profile(client, MediaKind.MOVIE, 693134)

    assert profile.imdb_id == "tt15239678"
    assert profile.title == "沙丘2"
    assert profile.original_title == "Dune: Part Two"
    assert profile.year == 2024
    assert profile.status == "Released"
    assert profile.poster_path == "/poster.jpg"
    assert profile.seasons == []
    assert profile.aliases == [
        "沙丘2",
        "Dune: Part Two",
        "沙丘：第二部",
        "Dune Part 2",
        "沙丘瀚战：第二章",
    ]


async def test_fetch_movie_uses_append_to_response() -> None:
    """整个电影建档只发一次请求，别名/译名/外部 ID 走 append_to_response 合并。"""
    captured: list[httpx.Request] = []
    client = _client({"/3/movie/693134": _MOVIE_DETAIL}, captured)
    await fetch_media_profile(client, MediaKind.MOVIE, 693134)

    assert len(captured) == 1
    params = dict(captured[0].url.params)
    assert params["append_to_response"] == "alternative_titles,translations,external_ids"
    assert params["language"] == "zh-CN"


_TV_DETAIL = {
    "id": 94997,
    "name": "龙之家族",
    "original_name": "House of the Dragon",
    "first_air_date": "2022-08-21",
    "status": "Returning Series",
    "poster_path": "/tv.jpg",
    "backdrop_path": None,
    "external_ids": {"imdb_id": "tt11198330"},
    # 剧集的 alternative_titles 用 "results" 键（TMDB 接口差异）
    "alternative_titles": {"results": [{"iso_3166_1": "TW", "title": "龍族前傳"}]},
    "translations": {"translations": []},
    "seasons": [
        {"season_number": 0},
        {"season_number": 1},
        {"season_number": 2},
    ],
}

_SEASONS = {
    "/3/tv/94997/season/0": {"name": "特别篇", "air_date": None, "episodes": []},
    "/3/tv/94997/season/1": {
        "name": "第 1 季",
        "air_date": "2022-08-21",
        "episodes": [
            {"episode_number": 1, "name": "龙之继承人", "air_date": "2022-08-21"},
            {"episode_number": 2, "name": "反叛的王子", "air_date": "2022-08-28"},
        ],
    },
    "/3/tv/94997/season/2": {
        "name": "第 2 季",
        "air_date": "2024-06-16",
        "episodes": [
            {"episode_number": 1, "name": "黑色之子", "air_date": "2024-06-16"},
            {"episode_number": 2, "name": None, "air_date": None},  # 未定档集
        ],
    },
}


async def test_fetch_tv_profile_with_seasons_and_episodes() -> None:
    """剧集档案：季按季号齐全（含特别季 0），集列表带播出日期，tv 别名键兼容。"""
    client = _client({"/3/tv/94997": _TV_DETAIL, **_SEASONS})
    profile = await fetch_media_profile(client, MediaKind.TV, 94997)

    assert profile.title == "龙之家族"
    assert profile.year == 2022
    assert "龍族前傳" in profile.aliases
    assert [s.season_number for s in profile.seasons] == [0, 1, 2]

    season1 = profile.seasons[1]
    assert season1.episode_count == 2
    assert season1.episodes[0].air_date == "2022-08-21"
    # 未定档集：air_date 为 None 而非假日期
    assert profile.seasons[2].episodes[1].air_date is None
    assert profile.seasons[2].episodes[1].name == ""


# ---------------------------------------------------------------------------
# 豆瓣收敛三分支
# ---------------------------------------------------------------------------


def _search_result(*items: dict) -> dict:
    return {"results": list(items)}


def _movie(tmdb_id: int, title: str, original: str, year: int) -> dict:
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original,
        "release_date": f"{year}-01-01",
        "poster_path": "/p.jpg",
    }


async def test_resolve_matched_when_unique_after_year_filter() -> None:
    """年份过滤后唯一 → 直接命中，无需用户确认。"""
    client = _client(
        {
            "/3/search/movie": _search_result(
                _movie(1, "沙丘2", "Dune: Part Two", 2024),
                _movie(2, "沙丘", "Dune", 1984),
            )
        }
    )
    result = await resolve_douban_to_tmdb(client, MediaKind.MOVIE, "沙丘2", year=2024)
    assert result.status is ResolveStatus.MATCHED
    assert result.tmdb_id == 1


async def test_resolve_matched_by_exact_title_and_year_among_many() -> None:
    """过滤后仍多个，但标题+年份精确相等者唯一 → 命中。"""
    client = _client(
        {
            "/3/search/movie": _search_result(
                _movie(1, "小丑", "Joker", 2019),
                _movie(2, "小丑回魂", "It", 2019),
            )
        }
    )
    result = await resolve_douban_to_tmdb(client, MediaKind.MOVIE, "小丑", year=2019)
    assert result.status is ResolveStatus.MATCHED
    assert result.tmdb_id == 1


async def test_resolve_ambiguous_returns_candidates() -> None:
    """无法唯一判定 → 歧义，候选交给弹层确认，绝不静默错配。"""
    client = _client(
        {
            "/3/search/movie": _search_result(
                _movie(1, "机器人总动员", "WALL·E", 2008),
                _movie(2, "机器人总动员2", "WALL·E 2", 2008),
            )
        }
    )
    result = await resolve_douban_to_tmdb(client, MediaKind.MOVIE, "机器人", year=2008)
    assert result.status is ResolveStatus.AMBIGUOUS
    assert [c.tmdb_id for c in result.candidates] == [1, 2]


async def test_resolve_not_found() -> None:
    """TMDB 未收录 → not_found（上层据此拒绝创建无锚条目）。"""
    client = _client({"/3/search/movie": _search_result()})
    result = await resolve_douban_to_tmdb(client, MediaKind.MOVIE, "极冷门条目", year=2001)
    assert result.status is ResolveStatus.NOT_FOUND
    assert result.candidates == []


async def test_resolve_year_mismatch_falls_back_to_all_candidates() -> None:
    """年份全对不上时退回全量候选做歧义确认——豆瓣年份可能有误。"""
    client = _client(
        {
            "/3/search/movie": _search_result(
                _movie(1, "某片", "Film A", 2010),
                _movie(2, "某片", "Film B", 2015),
            )
        }
    )
    result = await resolve_douban_to_tmdb(client, MediaKind.MOVIE, "某片", year=1990)
    assert result.status is ResolveStatus.AMBIGUOUS
    assert len(result.candidates) == 2
