"""发现接口的本地库存投影回归测试。

覆盖外部身份锚、在位文件过滤、多媒体库聚合，以及列表摘要和详情深链的响应
边界。上游发现服务均替换为内存桩，测试不依赖 TMDB 或豆瓣网络。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.discover_library import DiscoverLibraryProjectionService
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import FileSource, Library, LibraryFile, MediaItem, utcnow
from movieclaw_media.models import (
    MediaCard,
    MediaDetail,
    MediaFacts,
    MediaKind,
    MediaRow,
    MediaSource,
)


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'discover.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'discover-api.db'}")
    monkeypatch.setenv("SECRET_KEY_FILE", str(tmp_path / ".secret_key"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()

    from movieclaw_api.api.deps import require_login
    from movieclaw_api.app import create_app

    app = create_app()
    app.dependency_overrides[require_login] = lambda: "tester"
    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


async def _seed_library_status(db) -> dict[str, int]:
    """构造多库电影、单集剧集、豆瓣条目及不可标记的反例。"""
    async with db.session() as session:
        priority = Library(
            name="优先电影库",
            kind="movie",
            root_paths=["/media/priority"],
            sort_order=1,
        )
        secondary = Library(
            name="次要电影库",
            kind="movie",
            root_paths=["/media/secondary"],
            sort_order=2,
        )
        tv_library = Library(
            name="剧集库",
            kind="tv",
            root_paths=["/media/tv"],
            sort_order=3,
        )
        session.add_all([priority, secondary, tv_library])
        await session.flush()

        movie = MediaItem(
            kind="movie",
            tmdb_id=42,
            title="示例电影",
            original_title="Sample Movie",
        )
        douban_movie = MediaItem(
            kind="movie",
            tmdb_id=43,
            douban_id="db-42",
            title="豆瓣电影",
            original_title="Douban Movie",
        )
        tv = MediaItem(
            kind="tv",
            tmdb_id=7,
            title="示例剧集",
            original_title="Sample Show",
        )
        missing_only = MediaItem(
            kind="movie",
            tmdb_id=77,
            title="只有缺失文件",
            original_title="Missing Only",
        )
        same_name_other_id = MediaItem(
            kind="movie",
            tmdb_id=99,
            title="同名作品",
            original_title="Same Name",
        )
        session.add_all([movie, douban_movie, tv, missing_only, same_name_other_id])
        await session.flush()

        assert (
            priority.id is not None
            and secondary.id is not None
            and tv_library.id is not None
            and movie.id is not None
            and douban_movie.id is not None
            and tv.id is not None
            and missing_only.id is not None
            and same_name_other_id.id is not None
        )
        session.add_all(
            [
                LibraryFile(
                    library_id=priority.id,
                    media_item_id=movie.id,
                    file_path="/media/priority/sample-1080p.mkv",
                    source=FileSource.SCANNED,
                ),
                LibraryFile(
                    library_id=priority.id,
                    media_item_id=movie.id,
                    file_path="/media/priority/sample-2160p.mkv",
                    source=FileSource.SCANNED,
                ),
                LibraryFile(
                    library_id=secondary.id,
                    media_item_id=movie.id,
                    file_path="/media/secondary/sample-1080p.mkv",
                    source=FileSource.SCANNED,
                ),
                LibraryFile(
                    library_id=priority.id,
                    media_item_id=douban_movie.id,
                    file_path="/media/priority/douban.mkv",
                    source=FileSource.SCANNED,
                ),
                LibraryFile(
                    library_id=tv_library.id,
                    media_item_id=tv.id,
                    season_number=2,
                    episode_number=3,
                    file_path="/media/tv/sample-s02e03.mkv",
                    source=FileSource.SCANNED,
                ),
                LibraryFile(
                    library_id=priority.id,
                    media_item_id=missing_only.id,
                    file_path="/media/priority/missing.mkv",
                    source=FileSource.SCANNED,
                    missing_since=utcnow(),
                ),
                LibraryFile(
                    library_id=priority.id,
                    media_item_id=same_name_other_id.id,
                    file_path="/media/priority/same-name.mkv",
                    source=FileSource.SCANNED,
                ),
            ]
        )
        await session.commit()
        return {
            "movie": movie.id,
            "douban_movie": douban_movie.id,
            "tv": tv.id,
            "priority_library": priority.id,
            "secondary_library": secondary.id,
            "tv_library": tv_library.id,
        }


def _card(
    item_id: str,
    *,
    kind: MediaKind = MediaKind.MOVIE,
    source: MediaSource = MediaSource.TMDB,
    title: str = "示例电影",
) -> MediaCard:
    return MediaCard(
        id=item_id,
        source=source,
        type=kind,
        title=title,
        original_title=title,
        year=2024,
        rating=8.0,
        poster_url="https://example.test/poster.jpg",
    )


async def test_projection_matches_only_exact_external_ids_and_present_files(db) -> None:
    """TMDB/豆瓣、电影/剧集均精确匹配；同名异 ID 与 missing 台账不误标。"""
    ids = await _seed_library_status(db)
    movie = _card("42")
    douban = _card("db-42", source=MediaSource.DOUBAN, title="豆瓣电影")
    tv = _card("7", kind=MediaKind.TV, title="示例剧集")
    same_name_different_id = _card("100", title="同名作品")
    missing_only = _card("77", title="只有缺失文件")
    invalid_tmdb_id = _card("not-a-number")

    async with db.session() as session:
        projector = DiscoverLibraryProjectionService(session)
        await projector.apply_cards(
            [movie, douban, tv, same_name_different_id, missing_only, invalid_tmdb_id]
        )

    assert movie.library_status is not None
    assert movie.library_status.media_item_id == ids["movie"]
    assert movie.library_status.library_count == 2
    assert movie.library_status.file_count == 3
    assert douban.library_status is not None
    assert douban.library_status.media_item_id == ids["douban_movie"]
    assert tv.library_status is not None
    assert tv.library_status.media_item_id == ids["tv"]
    assert tv.library_status.library_count == 1 and tv.library_status.file_count == 1
    assert same_name_different_id.library_status is None
    assert missing_only.library_status is None
    assert invalid_tmdb_id.library_status is None
    assert "file_path" not in movie.model_dump()
    assert "audio_streams" not in movie.model_dump()


async def test_detail_links_are_sorted_and_related_cards_keep_summary_only(db) -> None:
    """详情主卡按库顺序给深链；相关推荐只复用轻量库存摘要。"""
    ids = await _seed_library_status(db)
    detail = MediaDetail(
        card=_card("42"),
        facts=MediaFacts(),
        related=[_card("db-42", source=MediaSource.DOUBAN, title="豆瓣电影")],
    )

    async with db.session() as session:
        await DiscoverLibraryProjectionService(session).apply_detail(detail)

    assert detail.card.library_status is not None
    assert [link.model_dump() for link in detail.library_links] == [
        {
            "library_id": ids["priority_library"],
            "library_name": "优先电影库",
            "media_item_id": ids["movie"],
        },
        {
            "library_id": ids["secondary_library"],
            "library_name": "次要电影库",
            "media_item_id": ids["movie"],
        },
    ]
    assert detail.related[0].library_status is not None
    assert detail.related[0].library_status.media_item_id == ids["douban_movie"]
    assert "library_links" not in detail.related[0].model_dump()


class _TmdbDiscoverStub:
    async def discover_hero(self, kind: MediaKind) -> list[MediaCard]:
        return [_card("42")]

    async def discover_row(self, kind: MediaKind, row_id: str) -> MediaRow:
        return MediaRow(
            id=row_id,
            title="剧集行",
            items=[_card("7", kind=MediaKind.TV, title="示例剧集")],
        )

    async def media_detail(self, kind: MediaKind, tmdb_id: int) -> MediaDetail:
        return MediaDetail(
            card=_card(str(tmdb_id)),
            facts=MediaFacts(),
            related=[_card("7", kind=MediaKind.TV, title="示例剧集")],
        )


class _DoubanDiscoverStub:
    async def full_collection(self, collection_id: str) -> MediaRow:
        return MediaRow(
            id=f"douban-{collection_id}",
            title="豆瓣榜单",
            items=[_card("db-42", source=MediaSource.DOUBAN, title="豆瓣电影")],
        )

    async def media_detail(self, douban_id: str) -> MediaDetail:
        return MediaDetail(
            card=_card(douban_id, source=MediaSource.DOUBAN, title="豆瓣电影"),
            facts=MediaFacts(),
        )


def test_discover_endpoints_serialize_summary_and_detail_links(
    client: TestClient, monkeypatch
) -> None:
    """Hero、行、豆瓣完整榜单和两类详情都走同一投影，列表不泄漏文件明细。"""
    from movieclaw_api.api.routes import discover as discover_route

    ids = client.portal.call(_seed_library_status, get_database())
    monkeypatch.setattr(discover_route, "get_media_service", lambda: _TmdbDiscoverStub())
    monkeypatch.setattr(discover_route, "get_douban_media_service", lambda: _DoubanDiscoverStub())

    hero = client.get("/api/v1/discover/movie/hero").json()["data"]
    assert hero[0]["library_status"] == {
        "media_item_id": ids["movie"],
        "library_count": 2,
        "file_count": 3,
    }
    assert "library_links" not in hero[0]
    assert not {"file_path", "audio_streams", "subtitle_streams"} & set(hero[0])

    row = client.get("/api/v1/discover/tv/rows/popular").json()["data"]
    assert row["items"][0]["library_status"]["media_item_id"] == ids["tv"]

    collection = client.get("/api/v1/discover/douban/collection/top250").json()["data"]
    assert collection["items"][0]["library_status"]["media_item_id"] == ids["douban_movie"]

    tmdb_detail = client.get("/api/v1/discover/movie/42").json()["data"]
    assert [link["library_name"] for link in tmdb_detail["library_links"]] == [
        "优先电影库",
        "次要电影库",
    ]
    assert tmdb_detail["related"][0]["library_status"]["media_item_id"] == ids["tv"]
    assert "library_links" not in tmdb_detail["related"][0]

    douban_detail = client.get("/api/v1/discover/douban/db-42").json()["data"]
    assert douban_detail["library_links"] == [
        {
            "library_id": ids["priority_library"],
            "library_name": "优先电影库",
            "media_item_id": ids["douban_movie"],
        }
    ]
