"""「订阅止于投递」两半的测试：库存对账关闭工单 + 投递救援巡检。

订阅不再亲自跟踪完成与搬运：工单完成状态由 library_file 库存对账推导
（任何入库路径都能关闭工单）；订阅只照看投递结果的死活（种子被删/卡死
→ 退回重新找资源）。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from sqlmodel import select

import movieclaw_api.services.download_progress as progress_mod
from movieclaw_api.core.config import get_settings
from movieclaw_api.services.wanted_fulfillment import close_fulfilled_wanted
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import (
    FileSource,
    LibraryFile,
    MediaItem,
    RuleSet,
    Subscription,
    SubscriptionActivity,
    WantedItem,
    WantedStatus,
    utcnow,
)
from movieclaw_db.repositories.library_repo import LibraryRepository


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'fulfill.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


async def _seed(db, *, wanted_status=WantedStatus.GRABBED, info_hash="abc123", grabbed_at=None):
    """建 库/条目/订阅/工单 的最小闭包，返回 (library_id, item_id, sub_id, wanted_id)。"""
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="剧集库", kind="tv", root_paths=["/media/tv"]
        )
        item = MediaItem(kind="tv", tmdb_id=200, title="测试剧集", original_title="Test", year=2024)
        rule_set = RuleSet(name="默认", spec={})
        session.add(item)
        session.add(rule_set)
        await session.commit()
        await session.refresh(item)
        await session.refresh(rule_set)
        sub = Subscription(
            media_item_id=item.id, kind="tv", rule_set_id=rule_set.id, library_id=library.id
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        wanted = WantedItem(
            subscription_id=sub.id,
            media_item_id=item.id,
            season_number=1,
            episode_number=1,
            status=wanted_status,
            info_hash=info_hash,
            grabbed_at=grabbed_at or utcnow(),
        )
        session.add(wanted)
        await session.commit()
        await session.refresh(wanted)
        return library.id, item.id, sub.id, wanted.id


@pytest.mark.asyncio
async def test_inventory_closes_wanted_and_records_activity(db):
    """库存出现在位单元 → 对应工单标记 imported + 时间线活动 + 状态重算。"""
    library_id, item_id, sub_id, wanted_id = await _seed(db)
    async with db.session() as session:
        session.add(
            LibraryFile(
                library_id=library_id,
                media_item_id=item_id,
                season_number=1,
                episode_number=1,
                file_path="/media/tv/测试剧集 (2024)/Season 01/测试剧集 (2024) - S01E01.mkv",
                size_bytes=1,
                source=FileSource.IMPORTED,
            )
        )
        await session.commit()

        closed = await close_fulfilled_wanted(session, item_id)
        assert closed == 1
        wanted = await session.get(WantedItem, wanted_id)
        assert wanted.status == WantedStatus.IMPORTED
        activities = list(
            (
                await session.execute(
                    select(SubscriptionActivity).where(
                        SubscriptionActivity.subscription_id == sub_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any(a.type == "imported" and "对账" in a.message for a in activities)

        # 幂等：再次对账无事发生
        assert await close_fulfilled_wanted(session, item_id) == 0


@pytest.mark.asyncio
async def test_inventory_ignores_unrelated_units(db):
    """库里只有别的集：工单保持开放。"""
    library_id, item_id, sub_id, wanted_id = await _seed(db)
    async with db.session() as session:
        session.add(
            LibraryFile(
                library_id=library_id,
                media_item_id=item_id,
                season_number=1,
                episode_number=2,  # 不是工单要的 E01
                file_path="/media/tv/x/e2.mkv",
                size_bytes=1,
                source=FileSource.SCANNED,
            )
        )
        await session.commit()
        assert await close_fulfilled_wanted(session, item_id) == 0
        wanted = await session.get(WantedItem, wanted_id)
        assert wanted.status == WantedStatus.GRABBED


@pytest.mark.asyncio
async def test_rescue_requeues_missing_torrent(db, monkeypatch):
    """救援巡检：种子在下载器中消失 → 工单退回 wanted 并记活动。"""
    _library_id, _item_id, sub_id, wanted_id = await _seed(db)

    async def query_none(info_hash, downloaders):
        return None

    monkeypatch.setattr(progress_mod, "_query_torrent", query_none)
    await progress_mod._rescue_group(sub_id, "abc123", downloaders=[])

    async with db.session() as session:
        wanted = await session.get(WantedItem, wanted_id)
        assert wanted.status == WantedStatus.WANTED
        assert wanted.info_hash is None
        assert wanted.next_search_at is not None
        activities = list(
            (
                await session.execute(
                    select(SubscriptionActivity).where(
                        SubscriptionActivity.subscription_id == sub_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any("不在下载器" in a.message for a in activities)


@pytest.mark.asyncio
async def test_rescue_requeues_stalled_torrent(db, monkeypatch):
    """救援巡检：投递超时仍未完成 → 视为卡死退回；未超时/已完成不动。"""
    from types import SimpleNamespace

    stale = utcnow() - timedelta(days=progress_mod.STALLED_REQUEUE_DAYS + 1)
    _library_id, _item_id, sub_id, wanted_id = await _seed(db, grabbed_at=stale)

    status = SimpleNamespace(name="Slow.Torrent", completed=False, progress=0.5)

    async def query_status(info_hash, downloaders):
        return status

    monkeypatch.setattr(progress_mod, "_query_torrent", query_status)
    await progress_mod._rescue_group(sub_id, "abc123", downloaders=[])

    async with db.session() as session:
        wanted = await session.get(WantedItem, wanted_id)
        assert wanted.status == WantedStatus.WANTED  # 卡死退回

    # 已完成待入库：救援不做任何事（搬运归监听导入/扫描，工单归库存对账）
    _library_id2, _item_id2, sub_id2, wanted_id2 = await _seed_second(db)
    status.completed = True
    await progress_mod._rescue_group(sub_id2, "def456", downloaders=[])
    async with db.session() as session:
        wanted = await session.get(WantedItem, wanted_id2)
        assert wanted.status == WantedStatus.GRABBED


async def _seed_second(db):
    """第二组样本（不同名称/哈希，避开唯一约束）。"""
    async with db.session() as session:
        library = await LibraryRepository(session).create(
            name="电影库", kind="movie", root_paths=["/media/movies"]
        )
        item = MediaItem(kind="movie", tmdb_id=300, title="某电影", original_title="M", year=2020)
        rule_set = RuleSet(name="规则二", spec={})
        session.add(item)
        session.add(rule_set)
        await session.commit()
        await session.refresh(item)
        await session.refresh(rule_set)
        sub = Subscription(
            media_item_id=item.id, kind="movie", rule_set_id=rule_set.id, library_id=library.id
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        wanted = WantedItem(
            subscription_id=sub.id,
            media_item_id=item.id,
            season_number=0,
            episode_number=0,
            status=WantedStatus.GRABBED,
            info_hash="def456",
            grabbed_at=utcnow(),
        )
        session.add(wanted)
        await session.commit()
        await session.refresh(wanted)
        return library.id, item.id, sub.id, wanted.id
