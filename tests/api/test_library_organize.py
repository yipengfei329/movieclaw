"""整理器（存量规范化）的测试。

覆盖：电影/剧集的规范目标路径计算、跳过规则（待识别/缺集号/多版本冲突/
目标被占用/原盘目录/缺失文件不参与）、执行后的物理改名 + 附属文件随迁 +
台账路径随迁 + 搬空目录清理、与扫描的双向互斥。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.library_organize as organize_mod
import movieclaw_api.services.library_scan as scan_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.library_organize import build_organize_plan, organize_library
from movieclaw_api.services.library_scan import scan_library
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import FileSource, Library, LibraryFile, MediaItem, utcnow
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_media.models import MediaKind


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'organize.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def _make_library(session, *, kind: MediaKind, root) -> Library:
    root.mkdir(parents=True, exist_ok=True)
    return await LibraryRepository(session).create(
        name=f"测试{kind.value}库", kind=kind.value, root_paths=[str(root)]
    )


async def _make_item(session, *, kind: MediaKind, tmdb_id: int, title: str, year: int) -> MediaItem:
    item = MediaItem(kind=kind.value, tmdb_id=tmdb_id, title=title, original_title=title, year=year)
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


def _add_file(session, library, item, path, *, season=0, episode=0, missing=False) -> LibraryFile:
    row = LibraryFile(
        library_id=library.id,
        media_item_id=item.id if item else None,
        season_number=season,
        episode_number=episode,
        file_path=str(path),
        size_bytes=path.stat().st_size if path.exists() else 0,
        source=FileSource.SCANNED,
        missing_since=utcnow() if missing else None,
    )
    session.add(row)
    return row


@pytest.mark.asyncio
async def test_movie_plan_targets_and_skips(db, tmp_path):
    """电影库：散乱文件 → 规范路径；待识别/缺失/多版本冲突/目标被占用各得其所。"""
    root = tmp_path / "movies"
    async with db.session() as session:
        library = await _make_library(session, kind=MediaKind.MOVIE, root=root)
        movie = await _make_item(
            session, kind=MediaKind.MOVIE, tmdb_id=300, title="某电影", year=2020
        )
        tidy = await _make_item(
            session, kind=MediaKind.MOVIE, tmdb_id=301, title="规整电影", year=2019
        )
        dupe = await _make_item(
            session, kind=MediaKind.MOVIE, tmdb_id=302, title="双版本", year=2021
        )

        # 散乱命名 + 一个字幕附属文件 + 一个同名异容器视频（独立版本，不算附属）
        messy_dir = root / "Some.Movie.2020.1080p.BluRay.x264-GRP"
        messy_dir.mkdir(parents=True)
        messy = messy_dir / "some.movie.2020.1080p.mkv"
        messy.write_bytes(b"main")
        (messy_dir / "some.movie.2020.1080p.zh.srt").write_bytes(b"sub")
        (messy_dir / "some.movie.2020.1080p.mp4").write_bytes(b"alt")
        _add_file(session, library, movie, messy)

        # 已符合规范：不动
        tidy_dir = root / "规整电影 (2019)"
        tidy_dir.mkdir(parents=True)
        tidy_file = tidy_dir / "规整电影 (2019).mkv"
        tidy_file.write_bytes(b"ok")
        _add_file(session, library, tidy, tidy_file)

        # 同条目两个同容器版本 → 规范名相同 → 双双跳过
        v1 = root / "双版本.v1.mkv"
        v2 = root / "双版本.v2.mkv"
        v1.write_bytes(b"v1")
        v2.write_bytes(b"v2")
        _add_file(session, library, dupe, v1)
        _add_file(session, library, dupe, v2)

        # 待识别 → 跳过；缺失 → 不参与
        unknown = root / "zzqx.mkv"
        unknown.write_bytes(b"?")
        _add_file(session, library, None, unknown)
        _add_file(session, library, movie, root / "gone.mkv", missing=True)
        await session.commit()

        plan = await build_organize_plan(session, library)

    assert plan.total == 5  # messy + tidy + v1 + v2 + unknown（missing 不计）
    assert plan.already_ok == 1
    assert len(plan.renames) == 1
    action = plan.renames[0]
    assert action.target_path == str(root / "某电影 (2020)" / "某电影 (2020).mkv")
    assert action.target_rel == "某电影 (2020)/某电影 (2020).mkv"
    # 字幕随迁、异容器视频不算附属
    assert [s.target_path for s in action.sidecars] == [
        str(root / "某电影 (2020)" / "某电影 (2020).zh.srt")
    ]
    reasons = {s.file_path: s.reason for s in plan.skips}
    assert "认领" in reasons[str(unknown)]
    assert "多版本" in reasons[str(v1)] and "多版本" in reasons[str(v2)]


@pytest.mark.asyncio
async def test_tv_plan_targets(db, tmp_path):
    """剧集库：季集号 → Season 目录 + SxxEyy 规范名；集号缺失跳过。"""
    root = tmp_path / "tv"
    async with db.session() as session:
        library = await _make_library(session, kind=MediaKind.TV, root=root)
        show = await _make_item(
            session, kind=MediaKind.TV, tmdb_id=200, title="测试剧集", year=2024
        )
        messy_dir = root / "乱七八糟剧" / "第二季"
        messy_dir.mkdir(parents=True)
        ep = messy_dir / "xx.s02e03.1080p.mkv"
        ep.write_bytes(b"e3")
        _add_file(session, library, show, ep, season=2, episode=3)
        noep = root / "乱七八糟剧" / "花絮.mkv"
        noep.write_bytes(b"x")
        _add_file(session, library, show, noep, season=0, episode=0)
        await session.commit()

        plan = await build_organize_plan(session, library)

    assert [a.target_path for a in plan.renames] == [
        str(root / "测试剧集 (2024)" / "Season 02" / "测试剧集 (2024) - S02E03.mkv")
    ]
    assert any("集号" in s.reason for s in plan.skips)


@pytest.mark.asyncio
async def test_organize_moves_files_and_relocates_ledger(db, tmp_path):
    """执行：物理改名 + 字幕随迁 + 台账路径随迁 + 搬空的目录被清理。"""
    root = tmp_path / "movies"
    async with db.session() as session:
        library = await _make_library(session, kind=MediaKind.MOVIE, root=root)
        movie = await _make_item(
            session, kind=MediaKind.MOVIE, tmdb_id=300, title="某电影", year=2020
        )
        messy_dir = root / "Some.Movie.2020" / "disc1"
        messy_dir.mkdir(parents=True)
        messy = messy_dir / "some.movie.mkv"
        messy.write_bytes(b"main")
        (messy_dir / "some.movie.zh.srt").write_bytes(b"sub")
        _add_file(session, library, movie, messy)
        await session.commit()
        library_id = library.id

    summary = await organize_library(library_id)

    assert summary.errors == []
    assert summary.renamed == 1
    assert summary.sidecars_renamed == 1
    target = root / "某电影 (2020)" / "某电影 (2020).mkv"
    assert target.read_bytes() == b"main"
    assert (root / "某电影 (2020)" / "某电影 (2020).zh.srt").read_bytes() == b"sub"
    # 搬空的 disc1 与 Some.Movie.2020 两级目录都被清掉，库根保留
    assert not (root / "Some.Movie.2020").exists()
    assert summary.removed_dirs == 2
    assert root.exists()

    async with db.session() as session:
        rows = list((await session.execute(select(LibraryFile))).scalars().all())
    assert [r.file_path for r in rows] == [str(target)]
    assert rows[0].missing_since is None


@pytest.mark.asyncio
async def test_organize_skips_occupied_target(db, tmp_path):
    """目标路径已被磁盘上的其他文件占用：跳过，绝不覆盖。"""
    root = tmp_path / "movies"
    async with db.session() as session:
        library = await _make_library(session, kind=MediaKind.MOVIE, root=root)
        movie = await _make_item(
            session, kind=MediaKind.MOVIE, tmdb_id=300, title="某电影", year=2020
        )
        messy = root / "some.movie.mkv"
        messy.write_bytes(b"main")
        _add_file(session, library, movie, messy)
        occupied = root / "某电影 (2020)"
        occupied.mkdir(parents=True)
        (occupied / "某电影 (2020).mkv").write_bytes(b"already-here")
        await session.commit()

        plan = await build_organize_plan(session, library)

    assert plan.renames == []
    assert any("覆盖" in s.reason for s in plan.skips)
    assert (occupied / "某电影 (2020).mkv").read_bytes() == b"already-here"


@pytest.mark.asyncio
async def test_scan_and_organize_are_mutually_exclusive(db, tmp_path):
    """整理与扫描双向互斥：任一方在跑，另一方立即让路不动磁盘。"""
    root = tmp_path / "movies"
    async with db.session() as session:
        library = await _make_library(session, kind=MediaKind.MOVIE, root=root)
        library_id = library.id

    scan_mod._scanning.add(library_id)
    try:
        summary = await organize_library(library_id)
        assert any("扫描" in e for e in summary.errors)
        assert summary.renamed == 0
    finally:
        scan_mod._scanning.discard(library_id)

    organize_mod._organizing.add(library_id)
    try:
        scan_summary = await scan_library(library_id)
        assert any("整理" in e for e in scan_summary.errors)
        assert scan_summary.scanned == 0
    finally:
        organize_mod._organizing.discard(library_id)
