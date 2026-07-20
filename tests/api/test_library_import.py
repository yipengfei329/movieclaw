"""入库管线（媒体库 L2）的端到端测试。

覆盖：下载完成检测（grabbed→downloaded）→ 整理器硬链+规范命名 →
library_file 落账 → 工单 imported → 时间线活动 → 订阅派生状态收紧；
以及种子被删除的退回语义、整理失败（无库）的退避重试语义。

下载器与 TMDB 均为假实现；文件系统用 tmp_path 真实硬链验证。
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from sqlalchemy import update
from sqlmodel import select

import movieclaw_api.services.download_progress as progress_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.download_progress import check_download_progress
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.subscription import SubscriptionService
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import (
    LibraryFile,
    SubscriptionActivity,
    SubscriptionStatus,
    WantedItem,
    WantedStatus,
)
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_downloader.models import TorrentFile, TorrentStatus
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"

_TV_ROUTES = {
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
        ],
    },
}


def _fake_tmdb() -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = _TV_ROUTES.get(request.url.path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'imp.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    progress_mod._import_backoff.clear()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def _setup_subscription(db, tmp_path, *, with_library: bool = True):
    """建库 + 订阅 S1（两集），把工单推进到 grabbed 并挂上 infohash。"""
    async with db.session() as session:
        library = None
        if with_library:
            library = await LibraryRepository(session).create(
                name="剧集库", kind="tv", root_paths=[str(tmp_path / "media" / "tv")]
            )
        service = SubscriptionService(session, MediaLibraryService(session, _fake_tmdb()))
        sub = await service.create(
            MediaKind.TV,
            200,
            selected_seasons=[1],
            library_id=library.id if library else None,
        )
        await session.execute(
            update(WantedItem)
            .where(WantedItem.subscription_id == sub.id)
            .values(status=WantedStatus.GRABBED, info_hash="a" * 40)
        )
        await session.commit()
    return sub


def _download_area(tmp_path) -> tuple[str, TorrentStatus]:
    """伪造下载区：整季包目录 + 两集视频 + 一个 sample 干扰文件。"""
    root = tmp_path / "downloads"
    pack = root / "Test.Show.S01.1080p.WEB-DL"
    pack.mkdir(parents=True)
    files = []
    for name in ("Test.Show.S01E01.1080p.mkv", "Test.Show.S01E02.1080p.mkv"):
        (pack / name).write_bytes(b"fake video " + name.encode())
        files.append(
            TorrentFile(path=f"{pack.name}/{name}", size_bytes=(pack / name).stat().st_size)
        )
    (pack / "sample.mkv").write_bytes(b"sample")
    files.append(TorrentFile(path=f"{pack.name}/sample.mkv", size_bytes=6))
    status = TorrentStatus(
        info_hash="a" * 40,
        name="Test.Show.S01.1080p.WEB-DL",
        progress=1.0,
        completed=True,
        save_path=str(root),
        files=files,
    )
    return str(root), status


def _patch_downloader(monkeypatch, status: TorrentStatus | None):
    """跳过真实下载器：可用列表非空 + 查询直接返回给定状态。"""

    async def fake_usable(_session):
        return [("fake", None)]

    async def fake_query(_info_hash, _downloaders):
        return status

    monkeypatch.setattr(progress_mod, "_usable_downloaders", fake_usable)
    monkeypatch.setattr(progress_mod, "_query_torrent", fake_query)


async def _wanted_rows(session, sub_id: int) -> list[WantedItem]:
    return list(
        (await session.execute(select(WantedItem).where(WantedItem.subscription_id == sub_id)))
        .scalars()
        .all()
    )


async def test_completed_torrent_imports_to_library(db, tmp_path, monkeypatch) -> None:
    """完成的整季包：两集硬链成规范名 → 落账 → imported → 订阅收齐。"""
    sub = await _setup_subscription(db, tmp_path)
    _root, status = _download_area(tmp_path)
    _patch_downloader(monkeypatch, status)

    await check_download_progress()

    # 硬链目标：规范目录 + 规范文件名
    season_dir = tmp_path / "media" / "tv" / "测试剧集 (2024)" / "Season 01"
    e1 = season_dir / "测试剧集 (2024) - S01E01.mkv"
    e2 = season_dir / "测试剧集 (2024) - S01E02.mkv"
    assert e1.exists() and e2.exists()
    # 硬链而非复制：与下载区同 inode
    src = tmp_path / "downloads" / "Test.Show.S01.1080p.WEB-DL" / "Test.Show.S01E01.1080p.mkv"
    assert e1.stat().st_ino == src.stat().st_ino
    # sample 不入库
    assert not (season_dir / "sample.mkv").exists()
    # L4：条目目录写出身份 NFO（tmdbid 反哺 Emby 与自家重扫）
    nfo = season_dir.parent / "tvshow.nfo"
    assert nfo.exists() and "<tmdbid>200</tmdbid>" in nfo.read_text(encoding="utf-8")

    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        assert {(f.season_number, f.episode_number) for f in files} == {(1, 1), (1, 2)}
        assert all(f.media_item_id is not None and f.source == "imported" for f in files)
        assert all(f.container == "mkv" for f in files)

        rows = await _wanted_rows(session, sub.id)
        assert all(w.status == WantedStatus.IMPORTED for w in rows)
        assert all(w.imported_at is not None and w.downloaded_at is not None for w in rows)

        activities = list(
            (
                await session.execute(
                    select(SubscriptionActivity).where(
                        SubscriptionActivity.subscription_id == sub.id
                    )
                )
            )
            .scalars()
            .all()
        )
        types = [a.type for a in activities]
        assert "downloaded" in types and "imported" in types
        imported = next(a for a in activities if a.type == "imported")
        assert "已入库" in imported.message and "剧集库" in imported.message
        assert len(imported.payload["files"]) == 2

        # 全部 imported 且不追新 → 订阅收齐
        sub_row = await session.get(type(sub), sub.id)
        assert sub_row.status == SubscriptionStatus.COMPLETED


async def test_incomplete_torrent_keeps_grabbed(db, tmp_path, monkeypatch) -> None:
    """未下完：状态不动，不做任何整理。"""
    sub = await _setup_subscription(db, tmp_path)
    _root, status = _download_area(tmp_path)
    status = status.model_copy(update={"progress": 0.5, "completed": False})
    _patch_downloader(monkeypatch, status)

    await check_download_progress()

    async with db.session() as session:
        rows = await _wanted_rows(session, sub.id)
        assert all(w.status == WantedStatus.GRABBED for w in rows)
        assert not (tmp_path / "media" / "tv" / "测试剧集 (2024)").exists()


async def test_missing_torrent_reverts_to_wanted(db, tmp_path, monkeypatch) -> None:
    """种子被手动删除：工单退回 wanted 冷却重搜，并记中文活动。"""
    sub = await _setup_subscription(db, tmp_path)
    _patch_downloader(monkeypatch, None)

    await check_download_progress()

    async with db.session() as session:
        rows = await _wanted_rows(session, sub.id)
        assert all(w.status == WantedStatus.WANTED and w.info_hash is None for w in rows)
        assert all(w.next_search_at is not None for w in rows)
        activities = list(
            (
                await session.execute(
                    select(SubscriptionActivity).where(
                        SubscriptionActivity.subscription_id == sub.id,
                        SubscriptionActivity.type == "dispatch_failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert activities and "不在下载器" in activities[0].message


async def test_import_failure_records_activity_and_backs_off(db, tmp_path, monkeypatch) -> None:
    """无媒体库可入：downloaded 停住 + IMPORT_FAILED 中文活动 + 退避不刷屏。"""
    sub = await _setup_subscription(db, tmp_path, with_library=False)
    _root, status = _download_area(tmp_path)
    _patch_downloader(monkeypatch, status)

    await check_download_progress()
    await check_download_progress()  # 第二轮落在退避窗口内，不应重复记活动

    async with db.session() as session:
        rows = await _wanted_rows(session, sub.id)
        assert all(w.status == WantedStatus.DOWNLOADED for w in rows)
        failures = list(
            (
                await session.execute(
                    select(SubscriptionActivity).where(
                        SubscriptionActivity.subscription_id == sub.id,
                        SubscriptionActivity.type == "import_failed",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(failures) == 1
        assert "未配置媒体库" in failures[0].message
