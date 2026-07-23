"""扫描完整性检测的测试：新文件 mtime 太新 → 暂缓入账，静默够久 → 正常入账。

库对根路径的用途不做假设——它完全可能同时是下载目录，写入中的半成品
不该进台账。补扫任务的到点行为靠 _arm_rescan 打桩验证参数，不真等待。
"""

from __future__ import annotations

import os
import time

import pytest
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.library_scan as scan_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.library_scan import scan_library
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import LibraryFile
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_media.models import MediaKind


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'scan.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    # 识别链不在本测试范围：TMDB 客户端打桩为惰性哑对象
    monkeypatch.setattr(scan_mod, "get_tmdb_client", lambda: object())
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def _make_library(db, root) -> int:
    root.mkdir(parents=True, exist_ok=True)
    async with db.session() as session:
        row = await LibraryRepository(session).create(
            name="测试电影库", kind=MediaKind.MOVIE.value, root_paths=[str(root)]
        )
        return row.id


def _age(path, seconds: int) -> None:
    """把文件 mtime 拨到 seconds 秒之前。"""
    past = time.time() - seconds
    os.utime(path, (past, past))


@pytest.mark.asyncio
async def test_fresh_file_deferred_then_ingested(db, tmp_path, monkeypatch):
    """mtime 太新的文件暂缓入账并安排补扫；静默够久后正常入账。"""
    root = tmp_path / "movies"
    library_id = await _make_library(db, root)
    video = root / "zzqx.mkv"
    video.write_bytes(b"downloading")  # mtime = 现在，疑似写入中

    armed: list[tuple[int, float]] = []
    monkeypatch.setattr(scan_mod, "_arm_rescan", lambda lid, delay: armed.append((lid, delay)))

    summary = await scan_library(library_id)
    assert summary.deferred == 1
    assert summary.scanned == 0
    async with db.session() as session:
        rows = list((await session.execute(select(LibraryFile))).scalars().all())
    assert rows == []  # 半成品绝不进台账
    # 已按剩余静默时间安排补扫
    assert armed and armed[0][0] == library_id
    assert 5.0 <= armed[0][1] <= scan_mod.NEW_FILE_QUIET_SECONDS

    # 静默够久（拨旧 mtime）：补扫入账
    _age(video, scan_mod.NEW_FILE_QUIET_SECONDS + 60)
    summary = await scan_library(library_id)
    assert summary.deferred == 0
    assert summary.scanned == 1
    async with db.session() as session:
        rows = list((await session.execute(select(LibraryFile))).scalars().all())
    assert [r.file_path for r in rows] == [str(video)]


@pytest.mark.asyncio
async def test_old_files_ingest_immediately(db, tmp_path, monkeypatch):
    """存量文件（mtime 久远）不受静默窗口影响，首轮即入账。"""
    root = tmp_path / "movies"
    library_id = await _make_library(db, root)
    video = root / "old.mkv"
    video.write_bytes(b"settled")
    _age(video, 3600)
    monkeypatch.setattr(scan_mod, "_arm_rescan", lambda *a: None)

    summary = await scan_library(library_id)
    assert summary.deferred == 0
    assert summary.scanned == 1
