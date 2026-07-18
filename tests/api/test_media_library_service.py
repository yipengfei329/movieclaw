"""MediaLibraryService 的落库编排测试：真实迁移 + MockTransport 假 TMDB。

同时覆盖 Phase 1 的两个验证点：
- 迁移产出的 media_item / media_season 表可用（fixture 里执行 run_migrations）；
- ensure_media_item 建档字段齐全、二次调用幂等、豆瓣收敛命中即建档回填。
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from sqlmodel import select

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import MediaSeason
from movieclaw_media.library import ResolveStatus
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"

_MOVIE_DETAIL = {
    "id": 693134,
    "title": "沙丘2",
    "original_title": "Dune: Part Two",
    "release_date": "2024-02-27",
    "status": "Released",
    "poster_path": "/poster.jpg",
    "backdrop_path": "/backdrop.jpg",
    "external_ids": {"imdb_id": "tt15239678"},
    "alternative_titles": {"titles": [{"iso_3166_1": "CN", "title": "沙丘：第二部"}]},
    "translations": {"translations": []},
}

_TV_DETAIL = {
    "id": 94997,
    "name": "龙之家族",
    "original_name": "House of the Dragon",
    "first_air_date": "2022-08-21",
    "status": "Returning Series",
    "poster_path": "/tv.jpg",
    "external_ids": {"imdb_id": "tt11198330"},
    "alternative_titles": {"results": []},
    "translations": {"translations": []},
    "seasons": [{"season_number": 1}],
}

_TV_SEASON_1 = {
    "name": "第 1 季",
    "air_date": "2022-08-21",
    "episodes": [
        {"episode_number": 1, "name": "龙之继承人", "air_date": "2022-08-21"},
        {"episode_number": 2, "name": "反叛的王子", "air_date": "2022-08-28"},
    ],
}

_ROUTES = {
    "/3/movie/693134": _MOVIE_DETAIL,
    "/3/tv/94997": _TV_DETAIL,
    "/3/tv/94997/season/1": _TV_SEASON_1,
    "/3/search/movie": {"results": [_MOVIE_DETAIL]},
}


def _fake_tmdb(captured: list[httpx.Request]) -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        payload = _ROUTES.get(request.url.path)
        if payload is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json=payload)

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """独立临时库 + 真实迁移，直接返回 Database 供 Service 层直测。"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'media.db'}")
    get_settings.cache_clear()
    settings = get_settings()
    init_db(settings.database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def test_ensure_movie_creates_item_with_full_identity(db) -> None:
    """电影建档：锚、外部 ID、标题、年份、别名、status、刷新台账齐全。"""
    captured: list[httpx.Request] = []
    async with db.session() as session:
        service = MediaLibraryService(session, _fake_tmdb(captured))
        item = await service.ensure_media_item(MediaKind.MOVIE, 693134)

    assert item.id is not None
    assert (item.kind, item.tmdb_id) == ("movie", 693134)
    assert item.imdb_id == "tt15239678"
    assert item.title == "沙丘2"
    assert item.year == 2024
    assert "沙丘：第二部" in item.aliases
    assert item.status == "Released"
    assert item.metadata_refreshed_at is not None
    assert item.next_refresh_at is None  # NULL=立即到期，交给刷新任务分档


async def test_ensure_is_idempotent_and_backfills_douban(db) -> None:
    """二次调用：不再请求 TMDB、复用同一行；带来的豆瓣身份与入口标题被回填。"""
    captured: list[httpx.Request] = []
    async with db.session() as session:
        service = MediaLibraryService(session, _fake_tmdb(captured))
        first = await service.ensure_media_item(MediaKind.MOVIE, 693134)
        requests_after_first = len(captured)

        second = await service.ensure_media_item(
            MediaKind.MOVIE,
            693134,
            douban_id="26608316",
            extra_aliases=["沙丘：第二部曲", "沙丘：第二部"],  # 后者已存在，应去重
        )

    assert second.id == first.id
    assert len(captured) == requests_after_first  # 幂等：零新增 TMDB 请求
    assert second.douban_id == "26608316"
    assert second.aliases.count("沙丘：第二部") == 1
    assert "沙丘：第二部曲" in second.aliases


async def test_ensure_tv_creates_seasons_with_episodes(db) -> None:
    """剧集建档：季与集列表一并落库，episodes JSON 结构与设计约定一致。"""
    captured: list[httpx.Request] = []
    async with db.session() as session:
        service = MediaLibraryService(session, _fake_tmdb(captured))
        item = await service.ensure_media_item(MediaKind.TV, 94997)

        result = await session.execute(
            select(MediaSeason).where(MediaSeason.media_item_id == item.id)
        )
        seasons = list(result.scalars().all())

    assert item.kind == "tv"
    assert len(seasons) == 1
    season = seasons[0]
    assert season.season_number == 1
    assert season.episode_count == 2
    assert season.episodes[0] == {
        "episode_number": 1,
        "name": "龙之继承人",
        "air_date": "2022-08-21",
    }


async def test_resolve_douban_matched_creates_item_with_source(db) -> None:
    """豆瓣收敛命中：直接建档，douban_id 与豆瓣标题别名随建档写入。"""
    captured: list[httpx.Request] = []
    async with db.session() as session:
        service = MediaLibraryService(session, _fake_tmdb(captured))
        resolution, item = await service.resolve_douban(
            MediaKind.MOVIE, "沙丘2", year=2024, douban_id="26608316"
        )

    assert resolution.status is ResolveStatus.MATCHED
    assert item is not None
    assert item.tmdb_id == 693134
    assert item.douban_id == "26608316"
    assert "沙丘2" in item.aliases


async def test_resolve_douban_not_found_creates_nothing(db) -> None:
    """收敛失败：不建无锚条目（search 返回空 → NOT_FOUND，条目为 None）。"""
    routes_empty = {"/3/search/movie": {"results": []}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=routes_empty.get(request.url.path, {}))

    client = TmdbClient(_KEY, transport=httpx.MockTransport(handler))
    async with db.session() as session:
        service = MediaLibraryService(session, client)
        resolution, item = await service.resolve_douban(MediaKind.MOVIE, "极冷门条目")

    assert resolution.status is ResolveStatus.NOT_FOUND
    assert item is None
