"""存量扫描器（媒体库 L3）的端到端测试。

覆盖：NFO 优先识别、目录名解析 + TMDB 保守收敛、待识别落账（NULL 锚）、
忽略规则、增量重扫跳过、订阅联通（wanted 跳过库存已有 + prepare 库存概览）、
对账任务（missing 标记与文件回归清除）、改名归并（身份随迁/人工认领保留/
复制与多候选不误并）。TMDB 为假实现。
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.library_scan as scan_mod
import movieclaw_api.services.media_discover as discover_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.library_scan import scan_library
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
    # 测试文件都是刚创建的，关掉"疑似写入中"静默窗口（该行为有专门测试覆盖）
    monkeypatch.setattr(scan_mod, "NEW_FILE_QUIET_SECONDS", 0)
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


async def test_scan_progress_observable_and_cleared(db, tmp_path, monkeypatch) -> None:
    """扫描进行中能轮询到 (已处理, 总数)，结束后进度清空——前端进度环的数据源。"""
    import asyncio

    import movieclaw_api.services.library_scan as scan_mod
    from movieclaw_api.services.library_scan import scan_progress

    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )

    # 给每个文件的探测加一点耗时，让"进行中"窗口足够被采样到
    original_probe = scan_mod.probe_media

    def slow_probe(path):
        import time

        time.sleep(0.05)
        return original_probe(path)

    monkeypatch.setattr(scan_mod, "probe_media", slow_probe)

    task = asyncio.create_task(scan_library(library.id))
    sampled: list[tuple[int, int]] = []
    while not task.done():
        progress = scan_progress(library.id)
        if progress is not None:
            sampled.append(progress)
        await asyncio.sleep(0.01)
    await task

    assert sampled, "扫描期间必须能采样到进度"
    assert sampled[-1][1] == 3  # 总数 = 两集 + junk
    assert scan_progress(library.id) is None  # 结束后清空


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
    # 删除感知已并入扫描本身：重扫即标记 missing（无需独立对账步骤）
    summary = await scan_library(library.id)
    assert summary.marked_missing == 1
    # 最近扫描结论要留档（前端"点了有反应"的反馈数据源）
    from movieclaw_api.services.library_scan import last_scan

    record = last_scan(library.id)
    assert record is not None and record[1].marked_missing == 1

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


async def test_scan_relinks_renamed_file(db, tmp_path) -> None:
    """改名归并：已识别文件在磁盘被改成认不出的名字 → 台账行随迁，身份无损。"""
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    old = root / "测试剧集 (2024)" / "Season 01" / "测试剧集.S01E01.1080p.mkv"
    new = old.with_name("完全认不出的名字.mkv")
    async with db.session() as session:
        row = (
            (await session.execute(select(LibraryFile).where(LibraryFile.file_path == str(old))))
            .scalars()
            .one()
        )
        old_id, old_item = row.id, row.media_item_id
        old_unit = (row.season_number, row.episode_number)
    old.rename(new)

    summary = await scan_library(library.id)
    assert summary.relinked == 1
    assert summary.unidentified == 0  # 没有当新文件进待识别

    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        assert len(files) == 3  # 行数不变：没有幽灵行 + 新行
        moved = next(f for f in files if f.file_path == str(new))
        assert moved.id == old_id  # 同一行随迁而非重建
        assert moved.media_item_id == old_item
        assert (moved.season_number, moved.episode_number) == old_unit
        assert moved.missing_since is None


async def test_scan_relink_preserves_manual_claim(db, tmp_path) -> None:
    """人工认领过的待识别文件被改名 → 认领成果随行保留，不用重新认领。"""
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    from movieclaw_db.repositories.library_file_repo import LibraryFileRepository

    junk = root / "未知内容目录" / "zzqx.mkv"
    async with db.session() as session:
        repo = LibraryFileRepository(session)
        row = (
            (await session.execute(select(LibraryFile).where(LibraryFile.file_path == str(junk))))
            .scalars()
            .one()
        )
        media_service = MediaLibraryService(session, _fake_tmdb())
        item = await media_service.ensure_media_item(MediaKind.TV, 200)
        await repo.claim_identity(row.id, media_item_id=item.id, season_number=1, episode_number=3)

    junk.rename(junk.with_name("还是认不出的新名字.mkv"))
    summary = await scan_library(library.id)
    assert summary.relinked == 1

    async with db.session() as session:
        moved = (
            (
                await session.execute(
                    select(LibraryFile).where(
                        LibraryFile.file_path.endswith("还是认不出的新名字.mkv")
                    )
                )
            )
            .scalars()
            .one()
        )
        assert moved.media_item_id is not None  # 认领结果延续
        assert (moved.season_number, moved.episode_number) == (1, 3)


async def test_scan_copy_is_not_relink(db, tmp_path) -> None:
    """复制（旧路径仍在磁盘）不是改名：不归并，按新文件落账。"""
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    src = root / "测试剧集 (2024)" / "Season 01" / "测试剧集.S01E01.1080p.mkv"
    copy = src.with_name("副本.mkv")
    copy.write_bytes(src.read_bytes())

    summary = await scan_library(library.id)
    assert summary.relinked == 0  # 旧路径仍在磁盘：不是改名，不归并

    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        assert len(files) == 4  # 副本按新文件落了新行（经目录名照常识别）
        assert {str(src), str(copy)} <= {f.file_path for f in files}


async def test_scan_relink_ambiguous_candidates_bail_out(db, tmp_path) -> None:
    """多个同尺寸旧行都消失时无法确定对应关系：不归并（宁缺毋滥）。"""
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    season_dir = root / "测试剧集 (2024)" / "Season 01"
    # E01/E02 内容同尺寸：一个删除、一个改名 → 新文件对应哪行无法确定
    (season_dir / "测试剧集.S01E01.1080p.mkv").unlink()
    (season_dir / "测试剧集.S01E02.1080p.mkv").rename(season_dir / "不知道是哪集.mkv")

    summary = await scan_library(library.id)
    assert summary.relinked == 0  # 两个候选二义：不归并

    async with db.session() as session:
        files = list((await session.execute(select(LibraryFile))).scalars().all())
        # 新文件落新行（经目录名照常识别），两条旧行原地保留（留给对账标记 missing）
        assert len(files) == 4


async def test_scan_records_unidentified_reason_and_claim_clears(db, tmp_path) -> None:
    """认不出的文件要在台账上留下"为什么认不出"（前端待识别清单展示）；
    人工认领后原因随之清除。"""
    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )
    await scan_library(library.id)

    async with db.session() as session:
        from movieclaw_db.repositories.library_file_repo import LibraryFileRepository

        repo = LibraryFileRepository(session)
        unknown = (await repo.list_unidentified(library_id=library.id))[0]
        assert unknown.unidentified_reason  # 有可读的失败原因
        identified = [
            f
            for f in await repo.list_by_library(library.id)
            if f.media_item_id is not None
        ]
        assert all(f.unidentified_reason is None for f in identified)

        # 认领后原因失义，应清除
        item_id = identified[0].media_item_id
        claimed = await repo.claim_identity(
            unknown.id, media_item_id=item_id, season_number=1, episode_number=9
        )
        assert claimed is not None and claimed.unidentified_reason is None


async def test_scan_stop_request_cancels_early(db, tmp_path) -> None:
    """停止请求让扫描提前收尾：cancelled 标记置位、剩余文件不入账；
    没有扫描在跑时 request_stop_scan 返回 False。"""
    from movieclaw_api.services.library_scan import request_stop_scan

    root = _make_tv_library(tmp_path)
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=[str(root)]
        )

    assert request_stop_scan(library.id) is False  # 没在扫

    # 预置停止标志：扫描循环在第一个文件前即检查到并提前收尾
    scan_mod._stop_requests.add(library.id)
    summary = await scan_library(library.id)
    assert summary.cancelled is True
    assert summary.scanned == 0  # 一个文件都没处理
    assert library.id not in scan_mod._stop_requests  # 收尾时清除标志

    # 停止不破坏增量语义：再扫一次照常完成
    summary2 = await scan_library(library.id)
    assert summary2.cancelled is False and summary2.identified == 2
