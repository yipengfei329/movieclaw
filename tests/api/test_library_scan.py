"""存量扫描器（媒体库 L3）的端到端测试。

覆盖：NFO 优先识别、目录名解析 + TMDB 保守收敛、待识别落账（NULL 锚）、
忽略规则、增量重扫跳过、订阅联通（wanted 跳过库存已有 + prepare 库存概览）、
对账任务（missing 标记与文件回归清除）。TMDB 为假实现。
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.library_scan as scan_mod
import movieclaw_api.services.media_discover as discover_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.library_scan import (
    _reconcile_missing,
    scan_library,
)
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.subscription import SubscriptionService
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import LibraryFile, WantedItem
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"

_ROUTES = {
    "/3/tv/200": {
        "id": 200,
        "name": "测试剧集",
        "original_name": "Test Show",
        "first_air_date": "2024-01-01",
        "status": "Returning Series",
        "external_ids": {},
        "alternative_titles": {"results": []},
        "translations": {"translations": []},
        "seasons": [{"season_number": 1}],
    },
    "/3/tv/200/season/1": {
        "name": "第 1 季",
        "air_date": "2024-01-01",
        "episodes": [
            {"episode_number": 1, "name": "E1", "air_date": "2024-01-01"},
            {"episode_number": 2, "name": "E2", "air_date": "2024-01-08"},
            {"episode_number": 3, "name": "E3", "air_date": "2024-01-15"},
        ],
    },
    "/3/movie/300": {
        "id": 300,
        "title": "某电影",
        "original_title": "Some Movie",
        "release_date": "2020-05-01",
        "status": "Released",
        "external_ids": {},
        "alternative_titles": {"titles": []},
        "translations": {"translations": []},
    },
}


def _fake_tmdb() -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/3/search/tv":
            query = request.url.params.get("query", "")
            results = (
                [
                    {
                        "id": 200,
                        "name": "测试剧集",
                        "original_name": "Test Show",
                        "first_air_date": "2024-01-01",
                    }
                ]
                if "测试剧集" in query or "Test Show" in query
                else []
            )
            return httpx.Response(200, json={"results": results})
        if path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        payload = _ROUTES.get(path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'scan.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    # 扫描器与认领路由取全局 TMDB 客户端：替换为假实现
    client = _fake_tmdb()
    monkeypatch.setattr(discover_mod, "get_tmdb_client", lambda: client)
    monkeypatch.setattr(scan_mod, "get_tmdb_client", lambda: client)
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


def _make_tv_library(tmp_path):
    """剧集库样本：规范目录两集 + 一个认不出的文件 + @eaDir 干扰。"""
    root = tmp_path / "media" / "tv"
    show = root / "测试剧集 (2024)" / "Season 01"
    show.mkdir(parents=True)
    (show / "测试剧集.S01E01.1080p.mkv").write_bytes(b"e1")
    (show / "测试剧集.S01E02.1080p.mkv").write_bytes(b"e2")
    junk = root / "未知内容目录" / "zzqx.mkv"
    junk.parent.mkdir(parents=True)
    junk.write_bytes(b"junk")
    eadir = root / "测试剧集 (2024)" / "@eaDir" / "thumb.mkv"
    eadir.parent.mkdir(parents=True)
    eadir.write_bytes(b"thumb")
    return root


async def test_scan_identifies_by_name_and_flags_unknown(db, tmp_path) -> None:
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )

    summary = await scan_library(library.id)
    assert summary.scanned == 3  # 两集 + junk；@eaDir 被忽略
    assert summary.identified == 2
    assert summary.unidentified == 1

    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        identified = [f for f in files if f.media_item_id is not None]
        assert {(f.season_number, f.episode_number) for f in identified} == {(1, 1), (1, 2)}
        assert all(f.source == "scanned" for f in files)
        unknown = [f for f in files if f.media_item_id is None]
        assert len(unknown) == 1 and unknown[0].file_path.endswith("zzqx.mkv")

    # 增量重扫：全部已知，秒过
    summary2 = await scan_library(library.id)
    assert summary2.scanned == 0 and summary2.skipped_known == 3


async def test_scan_prefers_nfo_identity(db, tmp_path) -> None:
    """电影库：文件名认不出，但目录里的 movie.nfo 带 tmdbid → 精确识别。"""
    root = tmp_path / "media" / "movies"
    folder = root / "乱七八糟的目录名"
    folder.mkdir(parents=True)
    (folder / "abcxyz.mkv").write_bytes(b"movie")
    (folder / "movie.nfo").write_text(
        "<movie><title>某电影</title><tmdbid>300</tmdbid></movie>", encoding="utf-8"
    )
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="电影库", kind="movie", root_paths=[str(root)]
        )

    summary = await scan_library(library.id)
    assert summary.identified == 1 and summary.unidentified == 0

    async with db.session() as session:
        row = (await session.execute(select(LibraryFile))).scalars().one()
        assert row.media_item_id is not None
        assert (row.season_number, row.episode_number) == (0, 0)


async def test_owned_units_skip_wanted_and_show_in_prepare(db, tmp_path) -> None:
    """库存联通：订阅创建只为缺的集建工单；prepare 返回每季已有集数。"""
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    async with db.session() as session:
        service = SubscriptionService(session, MediaLibraryService(session, _fake_tmdb()))
        # prepare：S1 已播 3 集，库里有 2 集
        item, seasons, _existing = await service.prepare(MediaKind.TV, 200)
        from movieclaw_db.repositories.library_file_repo import LibraryFileRepository

        owned = await LibraryFileRepository(session).owned_units(item.id)
        assert owned == {(1, 1), (1, 2)}

        sub = await service.create(MediaKind.TV, 200, selected_seasons=[1])
        rows = list(
            (await session.execute(select(WantedItem).where(WantedItem.subscription_id == sub.id)))
            .scalars()
            .all()
        )
        # 只有 E03 缺——E01/E02 库里已有，不建工单
        assert {(w.season_number, w.episode_number) for w in rows} == {(1, 3)}


async def test_reconcile_marks_missing_and_rescan_restores(db, tmp_path) -> None:
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    victim = root / "测试剧集 (2024)" / "Season 01" / "测试剧集.S01E01.1080p.mkv"
    payload = victim.read_bytes()
    victim.unlink()
    await _reconcile_missing(library.id)

    async with db.session() as session:
        row = (
            (await session.execute(select(LibraryFile).where(LibraryFile.file_path == str(victim))))
            .scalars()
            .one()
        )
        assert row.missing_since is not None  # 标记而非删除

    # 文件回归 → 重扫清除 missing
    victim.write_bytes(payload)
    await scan_library(library.id)
    async with db.session() as session:
        row = (
            (await session.execute(select(LibraryFile).where(LibraryFile.file_path == str(victim))))
            .scalars()
            .one()
        )
        assert row.missing_since is None
