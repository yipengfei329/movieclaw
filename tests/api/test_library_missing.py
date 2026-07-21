"""缺失清单（P0）的接口测试：按条目聚合、订阅提示、清理只删 missing 行。"""

from __future__ import annotations

import pytest_asyncio
from sqlmodel import select

from movieclaw_api.api.routes.libraries import clear_missing, list_missing
from movieclaw_api.core.config import get_settings
from movieclaw_api.schemas.library import MissingClearPayload
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import FileSource, LibraryFile, MediaItem, utcnow
from movieclaw_db.repositories.library_repo import LibraryRepository


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'missing.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def _seed(session) -> tuple[int, int, int]:
    """一个剧集库 + 两个条目：A 部分缺失（1 缺 1 在），B 全缺失（2 个文件）。"""
    library = await LibraryRepository(session).create(
        name="剧集库", kind="tv", root_paths=["/tv"]
    )
    item_a = MediaItem(kind="tv", tmdb_id=201, title="部分缺失剧", original_title="A")
    item_b = MediaItem(kind="tv", tmdb_id=202, title="全缺失剧", original_title="B")
    session.add_all([item_a, item_b])
    await session.flush()
    now = utcnow()
    session.add_all(
        [
            LibraryFile(
                library_id=library.id,
                media_item_id=item_a.id,
                season_number=1,
                episode_number=1,
                file_path="/tv/A/S01E01.mkv",
                size_bytes=1,
                source=FileSource.SCANNED,
                missing_since=now,
            ),
            LibraryFile(
                library_id=library.id,
                media_item_id=item_a.id,
                season_number=1,
                episode_number=2,
                file_path="/tv/A/S01E02.mkv",
                size_bytes=1,
                source=FileSource.SCANNED,
            ),
            LibraryFile(
                library_id=library.id,
                media_item_id=item_b.id,
                season_number=1,
                episode_number=1,
                file_path="/tv/B/S01E01.mkv",
                size_bytes=1,
                source=FileSource.SCANNED,
                missing_since=now,
            ),
            LibraryFile(
                library_id=library.id,
                media_item_id=item_b.id,
                season_number=2,
                episode_number=1,
                file_path="/tv/B/S02E01.mkv",
                size_bytes=1,
                source=FileSource.SCANNED,
                missing_since=now,
            ),
        ]
    )
    await session.flush()
    assert library.id and item_a.id and item_b.id
    return library.id, item_a.id, item_b.id


async def test_missing_list_aggregates_by_item(db) -> None:
    async with db.session() as session:
        library_id, item_a, item_b = await _seed(session)
        resp = await list_missing(library_id, session)
        rows = {r.media_item_id: r for r in resp.data}
        # 在位文件不进清单；A 只算缺的那 1 个，B 两个全算
        assert len(rows[item_a].files) == 1
        assert len(rows[item_b].files) == 2
        assert rows[item_a].subscription_id is None  # 无订阅不提示


async def test_clear_missing_per_item_and_all(db) -> None:
    async with db.session() as session:
        library_id, item_a, item_b = await _seed(session)

        # 按条目清：只删 A 的 missing 行，A 的在位行与 B 都不动
        resp = await clear_missing(
            MissingClearPayload(library_id=library_id, media_item_id=item_a), session
        )
        assert resp.data["cleared"] == 1

    # 跨会话断言：清理必须已提交（曾有只 flush 不 commit 的回归——200 但没删掉）
    async with db.session() as session:
        remaining = list((await session.execute(select(LibraryFile))).scalars().all())
        assert len(remaining) == 3
        assert all(
            f.missing_since is None for f in remaining if f.media_item_id == item_a
        )

        # 清整库：B 的两条 missing 行清掉，在位行保留
        resp = await clear_missing(MissingClearPayload(library_id=library_id), session)
        assert resp.data["cleared"] == 2

    async with db.session() as session:
        remaining = list((await session.execute(select(LibraryFile))).scalars().all())
        assert len(remaining) == 1 and remaining[0].missing_since is None


async def test_clear_unidentified_bulk(db) -> None:
    """批量忽略：只删 media_item_id 为空的待识别行，已识别的台账不动。"""
    from movieclaw_api.api.routes.libraries import clear_unidentified
    from movieclaw_api.schemas.library import UnidentifiedClearPayload

    async with db.session() as session:
        library_id, _, _ = await _seed(session)
        session.add_all(
            [
                LibraryFile(
                    library_id=library_id,
                    media_item_id=None,
                    season_number=0,
                    episode_number=0,
                    file_path=f"/tv/junk{i}.mkv",
                    size_bytes=1,
                    source=FileSource.SCANNED,
                )
                for i in range(3)
            ]
        )
        await session.flush()

        resp = await clear_unidentified(UnidentifiedClearPayload(library_id=library_id), session)
        assert resp.data["cleared"] == 3

    # 跨会话断言：忽略必须已提交
    async with db.session() as session:
        remaining = list((await session.execute(select(LibraryFile))).scalars().all())
        # _seed 的 4 条已识别台账（含 missing）全部保留
        assert len(remaining) == 4
        assert all(f.media_item_id is not None for f in remaining)
