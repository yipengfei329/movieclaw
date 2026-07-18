"""P4 管线的服务级测试：水位被动匹配、拒绝记录、整季包选优、认领竞态、
搜索退避、元数据刷新生长。全程 dry-run 投递（默认配置）。

夹具剧集同 test_subscription_service：S1 两集已播；S2 = E1 昨播/E2 十天后/E3 未定档。
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest_asyncio
from sqlmodel import select

from movieclaw_api.core.config import get_settings
from movieclaw_api.services.download_dispatch import dispatch
from movieclaw_api.services.media_library import MediaLibraryService
from movieclaw_api.services.rule_sets import RuleSetService
from movieclaw_api.services.subscription import SubscriptionService
from movieclaw_api.services.subscription_matching import evaluate_and_dispatch
from movieclaw_api.services.torrent_matcher import process_new_torrents
from movieclaw_db.engine import dispose_db, get_database, init_db
from movieclaw_db.migrations import run_migrations
from movieclaw_db.models import (
    SiteTorrent,
    SubscriptionActivity,
    TorrentSource,
    WantedItem,
    WantedStatus,
)
from movieclaw_db.models.base import utcnow
from movieclaw_enrich.models import TorrentAttrs
from movieclaw_matcher import RuleVerdict, TorrentCandidate
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"

_TODAY = utcnow().date()
_AIRED = (_TODAY - timedelta(days=10)).isoformat()
_YESTERDAY = (_TODAY - timedelta(days=1)).isoformat()
_FUTURE = (_TODAY + timedelta(days=10)).isoformat()

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
        "seasons": [{"season_number": 1}, {"season_number": 2}],
    },
    "/3/tv/200/season/1": {
        "name": "第 1 季",
        "air_date": "2024-01-01",
        "episodes": [
            {"episode_number": 1, "name": "E1", "air_date": _AIRED},
            {"episode_number": 2, "name": "E2", "air_date": _AIRED},
        ],
    },
    "/3/tv/200/season/2": {
        "name": "第 2 季",
        "air_date": _YESTERDAY,
        "episodes": [
            {"episode_number": 1, "name": "E1", "air_date": _YESTERDAY},
            {"episode_number": 2, "name": "E2", "air_date": _FUTURE},
            {"episode_number": 3, "name": "E3", "air_date": None},
        ],
    },
}


def _fake_tmdb(routes: dict) -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = routes.get(request.url.path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'pipe.db'}")
    get_settings.cache_clear()
    init_db(get_settings().database_url, echo=False)
    await run_migrations()
    yield get_database()
    await dispose_db()
    get_settings.cache_clear()


def _service(session) -> SubscriptionService:
    return SubscriptionService(session, MediaLibraryService(session, _fake_tmdb(_TV_ROUTES)))


async def _insert_torrent(session, torrent_id: str, title: str, attrs: dict, **kw) -> SiteTorrent:
    row = SiteTorrent(
        site_id="testsite",
        torrent_id=torrent_id,
        title=title,
        subtitle=kw.pop("subtitle", ""),
        attrs=attrs,
        enrich_version=1,
        source=TorrentSource.LIST,
        seeders=kw.pop("seeders", 10),
        download_volume_factor=kw.pop("dvf", 0.0),
        is_free=kw.pop("is_free", True),
        **kw,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _wanted_map(session, sub_id: int) -> dict[tuple[int, int], WantedItem]:
    rows = (
        (await session.execute(select(WantedItem).where(WantedItem.subscription_id == sub_id)))
        .scalars()
        .all()
    )
    return {(w.season_number, w.episode_number): w for w in rows}


async def _activities(session, sub_id: int) -> list[SubscriptionActivity]:
    return list(
        (
            await session.execute(
                select(SubscriptionActivity)
                .where(SubscriptionActivity.subscription_id == sub_id)
                .order_by(SubscriptionActivity.id)
            )
        )
        .scalars()
        .all()
    )


_S1_PACK_ATTRS = {
    "media_type": "tv",
    "year": 2024,
    "seasons": [1],
    "episodes": [1, 2],
    "complete": True,
    "resolution": "2160p",
}


# ---------------------------------------------------------------------------
# F2 被动匹配：水位语义 + dry-run 投递闭环
# ---------------------------------------------------------------------------


async def test_watermark_skips_history_then_follows_new_torrents(db) -> None:
    """首跑水位初始化=当前最大 id（历史缓存不参与——铁律）；此后新种子被跟随匹配。"""
    async with db.session() as session:
        sub = await _service(session).create(
            MediaKind.TV, 200, selected_seasons=[1, 2], follow_future=True
        )
        await _insert_torrent(
            session, "hist", "Test Show S01 2160p WEB-DL 历史种子", _S1_PACK_ATTRS
        )

    await process_new_torrents()  # 首跑：只初始化水位
    async with db.session() as session:
        wanted = await _wanted_map(session, sub.id)
        assert all(w.status == WantedStatus.WANTED for w in wanted.values())

        await _insert_torrent(
            session, "new1", "Test Show S01 2160p WEB-DL 新种子", _S1_PACK_ATTRS
        )

    await process_new_torrents()  # 二跑：跟随到新种子并投递
    async with db.session() as session:
        wanted = await _wanted_map(session, sub.id)
        assert wanted[(1, 1)].status == WantedStatus.GRABBED
        assert wanted[(1, 2)].status == WantedStatus.GRABBED
        assert wanted[(2, 1)].status == WantedStatus.WANTED  # 未被 S1 包覆盖

        grabbed = [a for a in await _activities(session, sub.id) if a.type == "grabbed"]
        assert len(grabbed) == 1
        assert "模拟投递" in grabbed[0].message
        assert grabbed[0].payload["dry_run"] is True
        assert sorted(grabbed[0].payload["units"]) == [[1, 1], [1, 2]]

    await process_new_torrents()  # 三跑：水位已推进，幂等无副作用
    async with db.session() as session:
        grabbed = [a for a in await _activities(session, sub.id) if a.type == "grabbed"]
        assert len(grabbed) == 1


async def test_rule_rejection_logged_once_with_reason(db) -> None:
    """身份命中但规则拒绝：记一条中文原因活动；同一候选不重复刷屏。"""
    async with db.session() as session:
        rule = await RuleSetService(session).create("只要4K", {"resolutions": ["2160p"]})
        sub = await _service(session).create(
            MediaKind.TV, 200, selected_seasons=[1], rule_set_id=rule.id
        )
        row = await _insert_torrent(
            session,
            "lowres",
            "Test Show S01 720p WEB-DL",
            {**_S1_PACK_ATTRS, "resolution": "720p"},
        )
        await evaluate_and_dispatch(session, [row], source="被动匹配")
        await evaluate_and_dispatch(session, [row], source="被动匹配")  # 重复评估

        activities = await _activities(session, sub.id)
        rejected = [a for a in activities if a.type == "match_rejected"]
        assert len(rejected) == 1  # 去重生效
        assert "720p 不在允许范围" in rejected[0].message
        assert rejected[0].payload["reason_code"] == "resolution_not_allowed"

        wanted = await _wanted_map(session, sub.id)
        assert all(w.status == WantedStatus.WANTED for w in wanted.values())


async def test_pack_preferred_over_higher_scored_single(db) -> None:
    """同批出现单集与整季包：整季包优先（已确认决策），单集不再重复投。"""
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1])
        single = await _insert_torrent(
            session,
            "single",
            "Test Show S01E01 2160p WEB-DL",
            {**_S1_PACK_ATTRS, "episodes": [1], "complete": None},
            seeders=500,
        )
        pack = await _insert_torrent(
            session, "pack", "Test Show S01 2160p WEB-DL", _S1_PACK_ATTRS, seeders=3
        )
        await evaluate_and_dispatch(session, [single, pack], source="被动匹配")

        grabbed = [a for a in await _activities(session, sub.id) if a.type == "grabbed"]
        assert len(grabbed) == 1
        assert grabbed[0].payload["torrent_id"] == "pack"
        assert sorted(grabbed[0].payload["units"]) == [[1, 1], [1, 2]]


async def test_dispatch_claim_race_second_caller_loses(db) -> None:
    """认领条件更新（防线①）：同一工单第二个投递方 0 行生效、直接放弃。"""
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1])
        wanted = await _wanted_map(session, sub.id)
        item = (await _service(session).detail(sub.id))[1]
        candidate = TorrentCandidate(
            site_id="testsite", torrent_id="x", title="Test Show S01 2160p",
            subtitle="", attrs=TorrentAttrs.model_validate(_S1_PACK_ATTRS),
        )
        verdict = RuleVerdict(accepted=True, score=1)
        targets = [wanted[(1, 1)], wanted[(1, 2)]]

        first = await dispatch(
            session, subscription=sub, item=item, wanted_rows=targets,
            candidate=candidate, verdict=verdict, source="测试",
        )
        second = await dispatch(
            session, subscription=sub, item=item, wanted_rows=targets,
            candidate=candidate, verdict=verdict, source="测试",
        )
    assert first is True and second is False


async def test_pack_covers_only_aired_by_publish_time(db) -> None:
    """真实教训回归：在播季的整季包只能满足"种子发布时已播出"的集。
    S2 = E1 昨播 / E2 十天后 / E3 未定档：今天发布的 S2 整季包只覆盖 E1，
    未播与未定档保持 wanted，订阅不得误判收齐。"""
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[2])
        pack = await _insert_torrent(
            session,
            "s2pack",
            "Test Show S02 2160p WEB-DL",
            {"media_type": "tv", "year": 2026, "seasons": [2], "resolution": "2160p"},
            publish_time=utcnow(),
        )
        await evaluate_and_dispatch(session, [pack], source="被动匹配")

        wanted = await _wanted_map(session, sub.id)
        assert wanted[(2, 1)].status == WantedStatus.GRABBED  # 已播：可满足
        assert wanted[(2, 2)].status == WantedStatus.WANTED  # 未播：物理上不可能在包里
        assert wanted[(2, 3)].status == WantedStatus.WANTED  # 未定档：无证据不覆盖


async def test_old_pack_cannot_cover_future_show(db) -> None:
    """发布时间早于所有集播出日期的整季包（同名他剧的典型形态）：覆盖为零，
    整次投递不发生。"""
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1, 2])
        old_pack = await _insert_torrent(
            session,
            "oldpack",
            "Test Show S01 2160p WEB-DL",
            {"media_type": "tv", "year": 2025, "seasons": [1], "resolution": "2160p"},
            publish_time=utcnow() - timedelta(days=400),  # 早于夹具所有集的播出日
        )
        await evaluate_and_dispatch(session, [old_pack], source="被动匹配")

        wanted = await _wanted_map(session, sub.id)
        assert all(w.status == WantedStatus.WANTED for w in wanted.values())


async def test_non_video_category_never_matches(db) -> None:
    """真实教训回归：《霸王别姬》原声专辑（标题含英文名+年份精确）曾胜出投递。
    站点分类明确为 music/game/av 的资源必须在粗筛剔除，进不了内核。"""
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1])
        soundtrack = await _insert_torrent(
            session,
            "ost",
            "原声大碟 - Test Show S01 2024 APE 整轨",
            {"year": 2024, "seasons": [1], "complete": True},
            category="music",
            seeders=999,
        )
        await evaluate_and_dispatch(session, [soundtrack], source="被动匹配")

        wanted = await _wanted_map(session, sub.id)
        assert all(w.status == WantedStatus.WANTED for w in wanted.values())
        activities = await _activities(session, sub.id)
        assert all(a.type == "created" for a in activities)  # 连拒绝记录都不该有


# ---------------------------------------------------------------------------
# F4 主动搜索：失败短冷却 / 未果退避 / 命中投递
# ---------------------------------------------------------------------------


def _fake_search(monkeypatch, *, sites_ok: int, hits: list, calls: list | None = None) -> None:
    from movieclaw_api.schemas.search import SearchResponse, SiteSearchStatus
    from movieclaw_api.services import site_search

    async def fake(keyword, categories=None, site_ids=None, label=None, page=1):
        if calls is not None:
            calls.append({"keyword": keyword, "categories": categories})
        statuses = [
            SiteSearchStatus(site_id=f"s{i}", site_name=f"站{i}", count=len(hits))
            for i in range(sites_ok)
        ]
        if sites_ok == 0:
            statuses = [
                SiteSearchStatus(site_id="s0", site_name="站0", count=0, error="站点访问失败")
            ]
        return SearchResponse(
            keyword=keyword, label=label, categories=[], total=len(hits),
            items=hits, sites=statuses,
        )

    monkeypatch.setattr(site_search, "search_all_sites", fake)


async def test_search_failure_short_retry_without_attempt(db, monkeypatch) -> None:
    """搜索本身失败：短冷却重试、不计退避档，活动如实解释。"""
    from movieclaw_api.services.wanted_search import search_wanted

    _fake_search(monkeypatch, sites_ok=0, hits=[])
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1])

    await search_wanted()
    async with db.session() as session:
        wanted = await _wanted_map(session, sub.id)
        for w in wanted.values():
            assert w.search_attempts == 0
            assert w.next_search_at > utcnow()  # 已顺延
        searched = [a for a in await _activities(session, sub.id) if a.type == "searched"]
        assert len(searched) == 1
        assert "未能执行" in searched[0].message


async def test_search_no_result_backs_off_with_attempt(db, monkeypatch) -> None:
    """搜索成功但无结果：计一次尝试、进退避曲线首档；且按订阅类型带分类过滤。"""
    from movieclaw_api.services.wanted_search import search_wanted
    from movieclaw_tracker.models import TorrentCategory

    calls: list = []
    _fake_search(monkeypatch, sites_ok=2, hits=[], calls=calls)
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1])

    await search_wanted()
    async with db.session() as session:
        wanted = await _wanted_map(session, sub.id)
        for w in wanted.values():
            assert w.search_attempts == 1
            assert w.last_search_at is not None
        searched = [a for a in await _activities(session, sub.id) if a.type == "searched"]
        assert "2 个站点返回 0 个结果" in searched[0].message

    # 剧集订阅的搜索必须带分类收窄（剧集/纪录片/动漫），不带 music/game/av 噪音
    assert calls and calls[0]["categories"] == [
        TorrentCategory.TV, TorrentCategory.DOCUMENTARY, TorrentCategory.ANIME
    ]


async def test_search_hit_persists_and_dispatches(db, monkeypatch) -> None:
    """搜索命中：结果落库（source=SEARCH）→ 共享管道投递 → 活动记全链路数字。"""
    from movieclaw_api.schemas.search import TorrentHit
    from movieclaw_api.services.wanted_search import search_wanted

    hit = TorrentHit(
        site_id="testsite",
        site_name="测试站",
        torrent_id="found1",
        title="Test Show S01 2160p WEB-DL Complete",
        subtitle="测试剧集 全2集",
        seeders=8,
        download_volume_factor=0.0,
        free=True,
        attrs=TorrentAttrs.model_validate(_S1_PACK_ATTRS),
    )
    _fake_search(monkeypatch, sites_ok=2, hits=[hit])
    async with db.session() as session:
        sub = await _service(session).create(MediaKind.TV, 200, selected_seasons=[1])

    await search_wanted()
    async with db.session() as session:
        wanted = await _wanted_map(session, sub.id)
        assert wanted[(1, 1)].status == WantedStatus.GRABBED
        assert wanted[(1, 2)].status == WantedStatus.GRABBED

        persisted = (
            await session.execute(
                select(SiteTorrent).where(SiteTorrent.torrent_id == "found1")
            )
        ).scalar_one()
        assert persisted.source == TorrentSource.SEARCH  # 副产品沉淀进公共缓存

        searched = [a for a in await _activities(session, sub.id) if a.type == "searched"]
        assert "投递覆盖 2 个单元" in searched[0].message


# ---------------------------------------------------------------------------
# F3 元数据刷新：新集生长 + 定档回填
# ---------------------------------------------------------------------------


async def test_refresh_grows_new_episode_and_schedules_dated(db, monkeypatch) -> None:
    """刷新发现新集 → 追新订阅补工单 + 活动；未定档集定档 → 回填调度。"""
    from movieclaw_api.services import media_refresh

    async with db.session() as session:
        sub = await _service(session).create(
            MediaKind.TV, 200, selected_seasons=[1], follow_future=True
        )
        before = await _wanted_map(session, sub.id)
        assert (2, 4) not in before
        assert before[(2, 3)].next_search_at is None  # 未定档

    updated_routes = {
        **_TV_ROUTES,
        "/3/tv/200/season/2": {
            "name": "第 2 季",
            "air_date": _YESTERDAY,
            "episodes": [
                {"episode_number": 1, "name": "E1", "air_date": _YESTERDAY},
                {"episode_number": 2, "name": "E2", "air_date": _FUTURE},
                {"episode_number": 3, "name": "E3", "air_date": _FUTURE},  # 定档了
                {"episode_number": 4, "name": "E4", "air_date": _FUTURE},  # 新集
            ],
        },
    }
    monkeypatch.setattr(media_refresh, "get_tmdb_client", lambda: _fake_tmdb(updated_routes))

    await media_refresh.refresh_media_metadata()
    async with db.session() as session:
        wanted = await _wanted_map(session, sub.id)
        assert (2, 4) in wanted  # 新集已生长
        assert wanted[(2, 3)].next_search_at is not None  # 定档回填调度

        activities = await _activities(session, sub.id)
        added = [a for a in activities if a.type == "wanted_added"]
        assert len(added) == 1
        assert "1 个新集" in added[0].message

        from movieclaw_db.models import MediaItem

        item = (
            await session.execute(select(MediaItem).where(MediaItem.tmdb_id == 200))
        ).scalar_one()
        assert item.next_refresh_at is not None  # 分档排期已写回
