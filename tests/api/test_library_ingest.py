"""下载监听导入的测试。

覆盖：完成检测（下载器权威信号优先、进行中标记阻断并重置计时、指纹
静默窗口、逐文件探测门禁）、硬链接/复制两种搬运策略、电影/剧集的规范
落位、台账幂等（同指纹不重复处理、指纹变化自动重试、季包增量补集）、
识别失败的失败记录、配置校验（监听目录与根路径重叠拒绝）。识别与季集
解析依赖 NER 模型与 TMDB，此处打桩——识别链本体由扫描器测试覆盖。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.library_ingest as ingest_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.exceptions import BadRequestException
from movieclaw_api.services.library_config import LibraryConfigService
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import IngestEntry, IngestStatus, Library, LibraryFile, MediaItem
from movieclaw_db.repositories.library_repo import LibraryRepository
from movieclaw_media.models import MediaKind

_FAKE_SPEC = SimpleNamespace(
    resolution="1080p",
    video_codec="hevc",
    hdr=None,
    bit_depth=10,
    duration_seconds=3600,
    bit_rate=None,
)


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'ingest.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    # 每个测试独立的静默观察表 + 立即落定的静默窗口（两次巡检即可导入：
    # 第一轮记录指纹，第二轮确认稳定）；下载器概览缓存清空（默认无下载器
    # → 权威信号缺席 → 走启发式路径）
    monkeypatch.setattr(ingest_mod, "_stability", {})
    monkeypatch.setattr(ingest_mod, "QUIET_SECONDS", 0)
    monkeypatch.setattr(ingest_mod, "_briefs_cache", (float("-inf"), None))
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def _make_library(db, *, kind: MediaKind, root, ingest_dirs=None) -> int:
    root.mkdir(parents=True, exist_ok=True)
    async with db.session() as session:
        row = await LibraryRepository(session).create(
            name=f"测试{kind.value}库",
            kind=kind.value,
            root_paths=[str(root)],
            ingest_dirs=ingest_dirs or [],
        )
        return row.id


async def _make_item(db, *, kind: MediaKind, title: str, year: int) -> MediaItem:
    async with db.session() as session:
        item = MediaItem(kind=kind.value, tmdb_id=300, title=title, original_title=title, year=year)
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item


async def _get_library(db, library_id: int) -> Library:
    async with db.session() as session:
        row = await session.get(Library, library_id)
        assert row is not None
        return row


def _stub_identify(monkeypatch, item):
    async def identify(session, kind, watch_root, main, spec):
        return item

    monkeypatch.setattr(ingest_mod, "_identify", identify)


async def _sweep_twice(db, library_id, watch, strategy="hardlink"):
    """两轮巡检：第一轮记录指纹，第二轮确认静默后处理。"""
    for _ in range(2):
        library = await _get_library(db, library_id)
        await ingest_mod._sweep_dir(library, str(watch), strategy)


@pytest.mark.asyncio
async def test_marker_blocks_and_resets_quiet_window(db, tmp_path, monkeypatch):
    """有下载中标记：任凭巡检多少轮都不入库；标记消失后重新静默再导入。"""
    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    item = await _make_item(db, kind=MediaKind.MOVIE, title="某电影", year=2020)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)

    entry = watch / "某电影 (2020)"
    entry.mkdir()
    (entry / "movie.mkv").write_bytes(b"video")
    marker = entry / "movie.mkv.aria2"
    marker.write_bytes(b"ctl")

    await _sweep_twice(db, library_id, watch)
    await _sweep_twice(db, library_id, watch)
    assert not (root / "某电影 (2020)").exists()

    marker.unlink()
    await _sweep_twice(db, library_id, watch)
    assert (root / "某电影 (2020)" / "某电影 (2020).mkv").read_bytes() == b"video"


@pytest.mark.asyncio
async def test_unstable_fingerprint_defers_import(db, tmp_path, monkeypatch):
    """指纹还在变化（写入中）：不导入；稳定后下一轮才导入。"""
    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    item = await _make_item(db, kind=MediaKind.MOVIE, title="某电影", year=2020)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)

    entry = watch / "某电影 (2020)"
    entry.mkdir()
    video = entry / "movie.mkv"
    video.write_bytes(b"part1")

    library = await _get_library(db, library_id)
    await ingest_mod._sweep_dir(library, str(watch), "hardlink")  # 记录指纹 A
    video.write_bytes(b"part1-part2")  # 下载继续，指纹变为 B
    await ingest_mod._sweep_dir(library, str(watch), "hardlink")  # B 首见，重新起算
    assert not (root / "某电影 (2020)").exists()
    await ingest_mod._sweep_dir(library, str(watch), "hardlink")  # B 稳定 → 导入
    assert (root / "某电影 (2020)" / "某电影 (2020).mkv").read_bytes() == b"part1-part2"


@pytest.mark.asyncio
async def test_movie_hardlink_import_and_ledger(db, tmp_path, monkeypatch):
    """电影硬链接入库：同 inode 零占用、台账落账、处理台账 imported、源文件不动。"""
    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    item = await _make_item(db, kind=MediaKind.MOVIE, title="某电影", year=2020)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)

    entry = watch / "Some.Movie.2020.1080p"
    entry.mkdir()
    src = entry / "some.movie.mkv"
    src.write_bytes(b"video")

    await _sweep_twice(db, library_id, watch)

    target = root / "某电影 (2020)" / "某电影 (2020).mkv"
    assert target.stat().st_ino == src.stat().st_ino  # 硬链接：同一 inode
    assert src.read_bytes() == b"video"  # 源文件原地保留（保种）
    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        records = list((await session.execute(select(IngestEntry))).scalars().all())
    assert [f.file_path for f in files] == [str(target)]
    assert files[0].resolution == "1080p"
    assert [r.status for r in records] == [IngestStatus.IMPORTED]
    assert records[0].imported_count == 1

    # 幂等：指纹未变，再巡检不重复处理
    await _sweep_twice(db, library_id, watch)
    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
    assert len(files) == 1


@pytest.mark.asyncio
async def test_copy_strategy(db, tmp_path, monkeypatch):
    """复制策略：目标是独立文件（不同 inode），内容一致。"""
    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    item = await _make_item(db, kind=MediaKind.MOVIE, title="某电影", year=2020)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)

    src = watch / "某电影 (2020).mkv"  # 裸文件条目
    src.write_bytes(b"video")

    await _sweep_twice(db, library_id, watch, strategy="copy")

    target = root / "某电影 (2020)" / "某电影 (2020).mkv"
    assert target.read_bytes() == b"video"
    assert target.stat().st_ino != src.stat().st_ino
    assert not target.with_name(target.name + ".part").exists()  # 临时文件已清


@pytest.mark.asyncio
async def test_probe_gate_blocks_partial_file(db, tmp_path, monkeypatch):
    """探测门禁：ffprobe 可用但主视频探测失败 → 记 failed，不搬运。"""
    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: None)
    monkeypatch.setattr(ingest_mod, "_ffprobe_available", lambda: True)

    entry = watch / "某电影 (2020)"
    entry.mkdir()
    (entry / "movie.mkv").write_bytes(b"partial")

    await _sweep_twice(db, library_id, watch)

    assert not (root / "某电影 (2020)").exists()
    async with db.session() as session:
        records = list((await session.execute(select(IngestEntry))).scalars().all())
    assert [r.status for r in records] == [IngestStatus.FAILED]
    assert "探测失败" in (records[0].message or "")


@pytest.mark.asyncio
async def test_identify_failure_retries_only_on_change(db, tmp_path, monkeypatch):
    """识别失败记 failed；指纹不变时退避不重试，指纹变化立即重试。"""
    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)

    calls = {"n": 0}

    async def identify_none(session, kind, watch_root, main, spec):
        calls["n"] += 1
        return None

    monkeypatch.setattr(ingest_mod, "_identify", identify_none)

    entry = watch / "unknown-release"
    entry.mkdir()
    video = entry / "video.mkv"
    video.write_bytes(b"x")

    await _sweep_twice(db, library_id, watch)
    assert calls["n"] == 1
    async with db.session() as session:
        record = (await session.execute(select(IngestEntry))).scalar_one()
    assert record.status == IngestStatus.FAILED

    # 指纹不变：退避期内不再尝试
    await _sweep_twice(db, library_id, watch)
    assert calls["n"] == 1

    # 指纹变化（如用户改名/补文件）：重新处理
    video.write_bytes(b"xy")
    await _sweep_twice(db, library_id, watch)
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_tv_import_and_incremental_episodes(db, tmp_path, monkeypatch):
    """剧集：按季集落 Season 目录；季包补集后指纹变化，增量导入新集。"""
    root, watch = tmp_path / "tv", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.TV, root=root)
    item = await _make_item(db, kind=MediaKind.TV, title="测试剧集", year=2024)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)
    # 季集解析依赖 NER 模型（测试环境缺失），按文件名打桩：epN.mkv → S01EN
    monkeypatch.setattr(
        ingest_mod, "_unit", lambda file, entry: (1, int(file.stem.removeprefix("ep")))
    )

    entry = watch / "测试剧集 S01"
    entry.mkdir()
    (entry / "ep1.mkv").write_bytes(b"e1")
    (entry / "ep2.mkv").write_bytes(b"e2")

    await _sweep_twice(db, library_id, watch)

    season_dir = root / "测试剧集 (2024)" / "Season 01"
    assert (season_dir / "测试剧集 (2024) - S01E01.mkv").read_bytes() == b"e1"
    assert (season_dir / "测试剧集 (2024) - S01E02.mkv").read_bytes() == b"e2"

    # 补集：指纹变化 → 重新处理，已在库的 E01/E02 幂等跳过，只新增 E03
    (entry / "ep3.mkv").write_bytes(b"e3")
    await _sweep_twice(db, library_id, watch)
    assert (season_dir / "测试剧集 (2024) - S01E03.mkv").read_bytes() == b"e3"
    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        record = (await session.execute(select(IngestEntry))).scalar_one()
    assert len(files) == 3
    assert record.imported_count == 3


@pytest.mark.asyncio
async def test_downloader_signal_is_authoritative(db, tmp_path, monkeypatch):
    """名称匹配到下载器种子：未完成时任凭静默也不导入；完成则单轮立即导入。"""
    from movieclaw_downloader import TorrentBrief

    root, watch = tmp_path / "movies", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.MOVIE, root=root)
    item = await _make_item(db, kind=MediaKind.MOVIE, title="某电影", year=2020)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "probe_media", lambda p: _FAKE_SPEC)

    brief = TorrentBrief(name="Some.Movie.2020", content_name="Some.Movie.2020", completed=False)

    async def briefs():
        return [brief]

    monkeypatch.setattr(ingest_mod, "_downloader_briefs", briefs)

    entry = watch / "Some.Movie.2020"
    entry.mkdir()
    (entry / "movie.mkv").write_bytes(b"video")

    # 下载器说没完成：静默窗口为 0 也不能导入（暂停种子的根治场景）
    await _sweep_twice(db, library_id, watch)
    await _sweep_twice(db, library_id, watch)
    assert not (root / "某电影 (2020)").exists()

    # 下载器确认完成：无需静默等待，单轮巡检立即导入
    brief.completed = True
    library = await _get_library(db, library_id)
    await ingest_mod._sweep_dir(library, str(watch), "hardlink")
    assert (root / "某电影 (2020)" / "某电影 (2020).mkv").read_bytes() == b"video"


@pytest.mark.asyncio
async def test_probe_gate_applies_per_file(db, tmp_path, monkeypatch):
    """探测门禁逐文件生效：季包里残缺的单集被拦下，完整的集照常入库。"""
    root, watch = tmp_path / "tv", tmp_path / "watch"
    watch.mkdir()
    library_id = await _make_library(db, kind=MediaKind.TV, root=root)
    item = await _make_item(db, kind=MediaKind.TV, title="测试剧集", year=2024)
    _stub_identify(monkeypatch, item)
    monkeypatch.setattr(ingest_mod, "_ffprobe_available", lambda: True)
    # ep2 残缺：探测失败；其余正常
    monkeypatch.setattr(
        ingest_mod, "probe_media", lambda p: None if "ep2" in str(p) else _FAKE_SPEC
    )
    monkeypatch.setattr(
        ingest_mod, "_unit", lambda file, entry: (1, int(file.stem.removeprefix("ep")))
    )

    entry = watch / "测试剧集 S01"
    entry.mkdir()
    (entry / "ep1.mkv").write_bytes(b"full-episode")  # 最大文件 = 主文件，探测通过
    (entry / "ep2.mkv").write_bytes(b"partial")

    await _sweep_twice(db, library_id, watch)

    season_dir = root / "测试剧集 (2024)" / "Season 01"
    assert (season_dir / "测试剧集 (2024) - S01E01.mkv").exists()
    assert not (season_dir / "测试剧集 (2024) - S01E02.mkv").exists()
    async with db.session() as session:
        record = (await session.execute(select(IngestEntry))).scalar_one()
    assert record.status == IngestStatus.IMPORTED
    assert record.imported_count == 1
    assert "探测失败" in (record.message or "")


@pytest.mark.asyncio
async def test_ingest_dir_must_not_overlap_roots(db, tmp_path):
    """配置校验：监听目录与库根路径前缀重叠直接拒绝。"""
    root = tmp_path / "movies"
    root.mkdir()
    async with db.session() as session:
        service = LibraryConfigService(session)
        with pytest.raises(BadRequestException):
            await service.create(
                name="重叠库",
                kind=MediaKind.MOVIE,
                root_paths=[str(root)],
                ingest_dirs=[{"path": str(root / "inbox"), "strategy": "hardlink"}],
            )
        with pytest.raises(BadRequestException):
            await service.create(
                name="策略错库",
                kind=MediaKind.MOVIE,
                root_paths=[str(root)],
                ingest_dirs=[{"path": str(tmp_path / "watch"), "strategy": "move"}],
            )
