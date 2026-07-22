"""媒体库 L4 与识别增强的测试。

覆盖：NFO 写出（不覆盖既有）、媒体服务器通知（成功/未配置/失败不抛）、
原盘目录识别（BDMV 整体一个条目）、电影时长消歧（歧义候选 ±2 分钟
唯一命中）、watchdog 实时监控（文件事件 → 去抖 → 增量扫描）。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.library_scan as scan_mod
import movieclaw_api.services.library_watch as watch_mod
import movieclaw_api.services.media_discover as discover_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.library_nfo import write_entry_nfo
from movieclaw_api.services.library_scan import scan_library
from movieclaw_api.services.media_probe import MediaSpec
from movieclaw_api.services.media_server_notify import notify_media_server_refresh
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import LibraryFile, MediaItem
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"

_MOVIE_DETAIL = {
    "external_ids": {},
    "alternative_titles": {"titles": []},
    "translations": {"translations": []},
    "status": "Released",
}

_ROUTES = {
    "/3/movie/400": {
        "id": 400,
        "title": "阿凡达",
        "original_title": "Avatar",
        "release_date": "2009-12-18",
        **_MOVIE_DETAIL,
    },
    "/3/movie/401": {
        "id": 401,
        "title": "两生花",
        "original_title": "Two Lives",
        "release_date": "1991-05-15",
        "runtime": 120,
        **_MOVIE_DETAIL,
    },
    "/3/movie/402": {
        "id": 402,
        "title": "两生花",
        "original_title": "Double Life",
        "release_date": "2011-03-02",
        "runtime": 90,
        **_MOVIE_DETAIL,
    },
}


def _fake_tmdb() -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/3/search/movie":
            query = request.url.params.get("query", "")
            if "阿凡达" in query:
                results = [
                    {
                        "id": 400,
                        "title": "阿凡达",
                        "original_title": "Avatar",
                        "release_date": "2009-12-18",
                    }
                ]
            elif query.startswith("两生"):
                # 同名双候选：无年份时靠时长消歧
                results = [
                    {"id": 401, "title": "两生花", "release_date": "1991-05-15"},
                    {"id": 402, "title": "两生花", "release_date": "2011-03-02"},
                ]
            else:
                results = []
            return httpx.Response(200, json={"results": results})
        if path == "/3/search/tv":
            return httpx.Response(200, json={"results": []})
        payload = _ROUTES.get(path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'l4.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    client = _fake_tmdb()
    monkeypatch.setattr(discover_mod, "get_tmdb_client", lambda: client)
    monkeypatch.setattr(scan_mod, "get_tmdb_client", lambda: client)
    # 测试文件都是刚创建的，关掉"疑似写入中"静默窗口（该行为有专门测试覆盖）
    monkeypatch.setattr(scan_mod, "NEW_FILE_QUIET_SECONDS", 0)
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# NFO 写出
# ---------------------------------------------------------------------------


def test_write_entry_nfo_and_respect_existing(tmp_path) -> None:
    item = MediaItem(
        kind="movie",
        tmdb_id=42,
        imdb_id="tt0042",
        title="某电影",
        original_title="Some Movie",
        year=2020,
        aliases=[],
    )
    entry = tmp_path / "某电影 (2020)"
    entry.mkdir()
    write_entry_nfo(entry, item)
    nfo = entry / "movie.nfo"
    text = nfo.read_text(encoding="utf-8")
    assert "<tmdbid>42</tmdbid>" in text and 'type="imdb"' in text

    # 既有 NFO 绝不覆盖（尊重 TMM/Emby 的刮削成果）
    nfo.write_text("precious", encoding="utf-8")
    write_entry_nfo(entry, item)
    assert nfo.read_text(encoding="utf-8") == "precious"


# ---------------------------------------------------------------------------
# 媒体服务器通知
# ---------------------------------------------------------------------------


async def test_media_server_notify(monkeypatch) -> None:
    # 未配置：no-op
    monkeypatch.setenv("MEDIA_SERVER_URL", "")
    get_settings.cache_clear()
    assert await notify_media_server_refresh() is False

    # 已配置：POST /Library/Refresh 带 token；失败（服务器 500）不抛只返回 False
    calls: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, ok: bool):
            self._ok = ok

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None):
            calls.append((url, (headers or {}).get("X-Emby-Token", "")))
            return httpx.Response(200 if self._ok else 500, request=httpx.Request("POST", url))

    monkeypatch.setenv("MEDIA_SERVER_URL", "http://emby:8096/")
    monkeypatch.setenv("MEDIA_SERVER_TOKEN", "tok123")
    get_settings.cache_clear()
    monkeypatch.setattr(
        "movieclaw_api.services.media_server_notify.httpx.AsyncClient",
        lambda timeout: _FakeClient(ok=True),
    )
    assert await notify_media_server_refresh() is True
    assert calls[-1] == ("http://emby:8096/Library/Refresh", "tok123")

    monkeypatch.setattr(
        "movieclaw_api.services.media_server_notify.httpx.AsyncClient",
        lambda timeout: _FakeClient(ok=False),
    )
    assert await notify_media_server_refresh() is False  # 失败不抛
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 原盘识别 + 时长消歧
# ---------------------------------------------------------------------------


async def test_scan_recognizes_bluray_disc(db, tmp_path) -> None:
    root = tmp_path / "movies"
    stream = root / "阿凡达 (2009)" / "BDMV" / "STREAM"
    stream.mkdir(parents=True)
    (stream / "00001.m2ts").write_bytes(b"x" * 100)
    (stream / "00002.m2ts").write_bytes(b"y" * 10)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="电影库", kind="movie", root_paths=[str(root)]
        )

    summary = await scan_library(library.id)
    assert summary.scanned == 1 and summary.identified == 1  # 整盘一个条目，不下钻

    async with db.session() as session:
        row = (await session.execute(select(LibraryFile))).scalars().one()
        assert row.container == "bluray"
        assert row.file_path.endswith("阿凡达 (2009)")
        assert row.size_bytes == 110  # 盘内文件总大小
        assert row.media_item_id is not None


async def test_movie_runtime_disambiguation(db, tmp_path, monkeypatch) -> None:
    """同名双候选、文件名无年份：实测 120 分钟 → 唯一命中 runtime=120 的候选。"""
    root = tmp_path / "movies"
    folder = root / "两生花"
    folder.mkdir(parents=True)
    (folder / "两生花.1080p.mkv").write_bytes(b"movie")
    monkeypatch.setattr(
        scan_mod,
        "probe_media",
        lambda _path: MediaSpec(
            resolution="1080p",
            video_codec="hevc",
            hdr=None,
            bit_depth=10,
            duration_seconds=120 * 60 + 30,
            bit_rate=None,
        ),
    )
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="电影库", kind="movie", root_paths=[str(root)]
        )

    summary = await scan_library(library.id)
    assert summary.identified == 1 and summary.unidentified == 0

    async with db.session() as session:
        row = (await session.execute(select(LibraryFile))).scalars().one()
        item = await session.get(MediaItem, row.media_item_id)
        assert item.tmdb_id == 401  # runtime 120 的那部，而非 90 的同名片


# ---------------------------------------------------------------------------
# watchdog 实时监控
# ---------------------------------------------------------------------------


async def test_watcher_triggers_incremental_scan(db, tmp_path, monkeypatch) -> None:
    pytest.importorskip("watchdog")
    # 缩短去抖窗口，测试秒级完成
    monkeypatch.setattr(watch_mod, "_QUIET_SECONDS", 0.2)
    monkeypatch.setattr(watch_mod, "_MAX_WAIT_SECONDS", 2.0)

    root = tmp_path / "movies"
    root.mkdir()
    async with db.session() as session:
        await LibraryRepository(session).create(name="电影库", kind="movie", root_paths=[str(root)])

    watcher = watch_mod.LibraryWatcher()
    await watcher.start()
    try:
        folder = root / "阿凡达 (2009)"
        folder.mkdir()
        (folder / "Avatar.2009.1080p.mkv").write_bytes(b"movie")
        # 等事件 → 去抖 → 扫描落账（轮询最多 10 秒）
        for _ in range(100):
            await asyncio.sleep(0.1)
            async with db.session() as session:
                rows = list((await session.execute(select(LibraryFile))).scalars().all())
            if rows:
                break
        assert rows, "监控未在 10 秒内触发增量扫描"
        assert rows[0].file_path.endswith("Avatar.2009.1080p.mkv")
    finally:
        await watcher.stop()
