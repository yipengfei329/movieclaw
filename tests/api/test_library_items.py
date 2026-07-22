"""库存聚合接口的播出状态与缺集统计：海报悬浮三分支（追新/补齐/已入库）的数据依据。"""

from __future__ import annotations

import pytest_asyncio

from movieclaw_api.api.routes.libraries import list_library_items
from movieclaw_api.core.config import get_settings
from movieclaw_api.schemas.library import derive_air_status
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import FileSource, LibraryFile, MediaItem, MediaSeason, utcnow
from movieclaw_db.repositories.library_repo import LibraryRepository


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'items.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


def _season(item_id: int, number: int, air_dates: list[str | None]) -> MediaSeason:
    """构造一季：按顺序给每集编号 1..N，air_date 逐集指定（None=未定档）。"""
    return MediaSeason(
        media_item_id=item_id,
        season_number=number,
        episodes=[
            {"episode_number": i + 1, "name": "", "air_date": d}
            for i, d in enumerate(air_dates)
        ],
    )


def _file(
    library_id: int, item_id: int, season: int, episode: int, *, missing: bool = False
) -> LibraryFile:
    return LibraryFile(
        library_id=library_id,
        media_item_id=item_id,
        season_number=season,
        episode_number=episode,
        file_path=f"/tv/{item_id}/S{season:02d}E{episode:02d}.mkv",
        size_bytes=1,
        source=FileSource.SCANNED,
        missing_since=utcnow() if missing else None,
    )


async def test_items_air_status_and_missing_episodes(db) -> None:
    """三分支数据齐备：在播剧带 airing；完结缺集算已播−在位；齐全为 0。"""
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=["/tv"]
        )
        airing = MediaItem(
            kind="tv", tmdb_id=301, title="在播剧", original_title="A", status="Returning Series"
        )
        ended_gap = MediaItem(
            kind="tv", tmdb_id=302, title="完结缺集剧", original_title="B", status="Ended"
        )
        ended_full = MediaItem(
            kind="tv", tmdb_id=303, title="完结齐全剧", original_title="C", status="Canceled"
        )
        session.add_all([airing, ended_gap, ended_full])
        await session.flush()
        assert library.id and airing.id and ended_gap.id and ended_full.id

        session.add_all(
            [
                # 在播剧：S1 已播 3 集，库里在位 2 集
                _season(airing.id, 1, ["2020-01-01", "2020-01-08", "2020-01-15"]),
                _file(library.id, airing.id, 1, 1),
                _file(library.id, airing.id, 1, 2),
                # 完结缺集剧：S1 已播 2 集 + 1 集未定档；在位 1 集，另 1 集文件缺失
                # （missing 不算拥有）→ 缺 1 集。特别季 0 有已播集但不参与统计。
                _season(ended_gap.id, 1, ["2020-01-01", "2020-01-08", None]),
                _season(ended_gap.id, 0, ["2020-06-01"]),
                _file(library.id, ended_gap.id, 1, 1),
                _file(library.id, ended_gap.id, 1, 2, missing=True),
                # 完结齐全剧：S1 已播 2 集全在位
                _season(ended_full.id, 1, ["2020-01-01", "2020-01-08"]),
                _file(library.id, ended_full.id, 1, 1),
                _file(library.id, ended_full.id, 1, 2),
            ]
        )
        await session.flush()

        resp = await list_library_items(library.id, session)
        rows = {r.media_item_id: r for r in resp.data}

        assert rows[airing.id].air_status == "airing"
        assert rows[airing.id].missing_episode_count == 1

        assert rows[ended_gap.id].air_status == "ended"
        assert rows[ended_gap.id].missing_episode_count == 1

        assert rows[ended_full.id].air_status == "ended"
        assert rows[ended_full.id].missing_episode_count == 0


async def test_items_movie_and_unknown_status(db) -> None:
    """电影不参与播出状态判断；剧集 status 未知/映射外时不猜（None）。"""
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="电影库", kind="movie", root_paths=["/movies"]
        )
        movie = MediaItem(
            kind="movie", tmdb_id=401, title="某电影", original_title="M", status="Released"
        )
        unknown = MediaItem(kind="tv", tmdb_id=402, title="无状态剧", original_title="U")
        session.add_all([movie, unknown])
        await session.flush()
        assert library.id and movie.id and unknown.id

        session.add_all(
            [
                LibraryFile(
                    library_id=library.id,
                    media_item_id=movie.id,
                    season_number=0,
                    episode_number=0,
                    file_path="/movies/M/M.mkv",
                    size_bytes=1,
                    source=FileSource.SCANNED,
                ),
                _season(unknown.id, 1, ["2020-01-01"]),
                _file(library.id, unknown.id, 1, 1),
            ]
        )
        await session.flush()

        resp = await list_library_items(library.id, session)
        rows = {r.media_item_id: r for r in resp.data}

        assert rows[movie.id].air_status is None
        assert rows[movie.id].missing_episode_count == 0
        assert rows[unknown.id].air_status is None


def test_derive_air_status_mapping() -> None:
    assert derive_air_status("Returning Series") == "airing"
    assert derive_air_status("In Production") == "airing"
    assert derive_air_status("Ended") == "ended"
    assert derive_air_status("Canceled") == "ended"
    assert derive_air_status("莫名其妙的值") is None
    assert derive_air_status(None) is None
