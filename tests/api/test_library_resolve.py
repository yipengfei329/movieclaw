"""扫描收敛验证器的判定规则测试。

每个用例对应一个真实种子实测暴露的失效模式（2026-07-21 批量测试）：
唯一候选绕过校验、季包年份≠首播年、标点差异、衍生条目干扰、同名双版本。
TMDB 为 MockTransport 假实现。
"""

from __future__ import annotations

import httpx

from movieclaw_api.services.library_resolve import (
    LocalEvidence,
    normalize_title,
    parse_total_episodes,
    verify_resolve,
)
from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

_KEY = "0123456789abcdef0123456789abcdef"


def _client(routes: dict[str, dict]) -> TmdbClient:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = routes.get(request.url.path)
        return httpx.Response(200 if payload is not None else 404, json=payload or {})

    return TmdbClient(_KEY, transport=httpx.MockTransport(handler))


def _movie(tmdb_id: int, title: str, original: str, year: int | None) -> dict:
    return {
        "id": tmdb_id,
        "title": title,
        "original_title": original,
        "release_date": f"{year}-01-01" if year else "",
    }


def _tv(tmdb_id: int, name: str, original: str, year: int | None) -> dict:
    return {
        "id": tmdb_id,
        "name": name,
        "original_name": original,
        "first_air_date": f"{year}-01-01" if year else "",
    }


def _movie_detail(tmdb_id: int, *, runtime: int | None = None, alts: list[str] = []) -> dict:
    return {
        "id": tmdb_id,
        "runtime": runtime,
        "alternative_titles": {"titles": [{"title": t} for t in alts]},
    }


def _tv_detail(
    tmdb_id: int,
    *,
    seasons: int | None = None,
    episode_counts: dict[int, int] | None = None,
    alts: list[str] = [],
) -> dict:
    return {
        "id": tmdb_id,
        "number_of_seasons": seasons,
        "seasons": [
            {"season_number": n, "episode_count": c} for n, c in (episode_counts or {}).items()
        ],
        "alternative_titles": {"results": [{"title": t} for t in alts]},
    }


def test_normalize_title_strips_punctuation() -> None:
    """点分/冒号/撇号归一后相等——FBI International 类失效的根治。"""
    assert normalize_title("FBI International") == normalize_title("FBI: International")
    assert normalize_title("13.Reasons.Why") == normalize_title("13 Reasons Why")
    assert normalize_title("Whos.Talking") == normalize_title("Who's Talking")


async def test_unique_result_with_year_counter_rejected() -> None:
    """「心墙 2019」错挂 1965 老片的 bug：唯一候选也必须过年份反证。"""
    client = _client(
        {
            "/3/search/movie": {"results": [_movie(32609, "心墙魅影", "The Collector", 1965)]},
            "/3/movie/32609": _movie_detail(32609, alts=["心墙"]),
        }
    )
    picked = await verify_resolve(client, MediaKind.MOVIE, LocalEvidence(title="心墙", year=2019))
    assert picked is None


async def test_unique_result_failing_title_gate_rejected() -> None:
    """Lonesome Dove → Lonesome Dove Church 类错配：标题门槛不过直接淘汰。"""
    client = _client(
        {
            "/3/search/movie": {
                "results": [_movie(326045, "Lonesome Dove Church", "Lonesome Dove Church", 2014)]
            },
            "/3/movie/326045": _movie_detail(326045),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.MOVIE, LocalEvidence(title="Lonesome Dove", year=1989)
    )
    assert picked is None


async def test_tv_no_year_spinoffs_fail_gate() -> None:
    """Better Call Saul S06：衍生条目不过标题门槛，正主唯一幸存即命中。"""
    client = _client(
        {
            "/3/search/tv": {
                "results": [
                    _tv(60059, "绝命律师", "Better Call Saul", 2015),
                    _tv(999, "Better Call Saul: Employee Training", "Better Call Saul: Employee Training", 2017),
                ]
            },
            "/3/tv/60059": _tv_detail(60059, seasons=6, episode_counts={6: 13}),
            "/3/tv/999": _tv_detail(999, seasons=2),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.TV, LocalEvidence(title="Better Call Saul", season=6, episode=1)
    )
    assert picked == 60059


async def test_tv_release_year_not_counter_evidence() -> None:
    """Breaking Bad S03 2011：季包年份是发布年（首播 2008），不作反证。"""
    client = _client(
        {
            "/3/search/tv": {"results": [_tv(1396, "绝命毒师", "Breaking Bad", 2008)]},
            "/3/tv/1396": _tv_detail(1396, seasons=5, episode_counts={3: 13}),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.TV, LocalEvidence(title="Breaking Bad", year=2011, season=3, episode=1)
    )
    assert picked == 1396


async def test_tv_season_count_discriminates_same_title() -> None:
    """Shark Tank S14：美/澳版同名，季数 ≥14 反证淘汰澳版，美版胜出。"""
    client = _client(
        {
            "/3/search/tv": {
                "results": [
                    _tv(30703, "创智赢家", "Shark Tank", 2009),
                    _tv(64002, "Shark Tank", "Shark Tank", 2015),
                ]
            },
            "/3/tv/30703": _tv_detail(30703, seasons=16, episode_counts={14: 22}),
            "/3/tv/64002": _tv_detail(64002, seasons=4),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.TV, LocalEvidence(title="Shark Tank", season=14, episode=4)
    )
    assert picked == 30703


async def test_tv_alt_title_gate_with_year_pick() -> None:
    """The Day 2018：正主靠 alternative titles 过门槛 + 年份佐证；
    WWE The Day Of 不过门槛，不再是"唯一候选即命中"的受害场景。"""
    client = _client(
        {
            "/3/search/tv": {
                "results": [
                    _tv(248223, "WWE The Day Of", "WWE The Day Of", 2017),
                    _tv(82178, "危情一日", "De Dag", 2018),
                ]
            },
            "/3/tv/248223": _tv_detail(248223, seasons=1),
            "/3/tv/82178": _tv_detail(82178, seasons=1, alts=["The Day"]),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.TV, LocalEvidence(title="The Day", year=2018, season=1)
    )
    assert picked == 82178


async def test_movie_exact_year_tiebreak() -> None:
    """The Man from Rome 2022：两个英文别名相同的候选，年份精确相等者胜出。"""
    client = _client(
        {
            "/3/search/movie": {
                "results": [
                    _movie(1101763, "罗马来客", "De man uit Rome", 2023),
                    _movie(1029862, "来自罗马的男人", "La piel del tambor", 2022),
                ]
            },
            "/3/movie/1101763": _movie_detail(1101763, alts=["The Man from Rome"]),
            "/3/movie/1029862": _movie_detail(1029862, alts=["The Man from Rome"]),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.MOVIE, LocalEvidence(title="The Man from Rome", year=2022)
    )
    assert picked == 1029862


async def test_movie_runtime_tiebreak_without_year() -> None:
    """无年份电影：同名双版本靠实测时长强吻合唯一者胜出。"""
    client = _client(
        {
            "/3/search/movie": {
                "results": [
                    _movie(1, "惊声尖叫", "Scream", 1996),
                    _movie(2, "惊声尖叫", "Scream", 2022),
                ]
            },
            "/3/movie/1": _movie_detail(1, runtime=111),
            "/3/movie/2": _movie_detail(2, runtime=134),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.MOVIE, LocalEvidence(title="Scream", duration_seconds=111 * 60 + 20)
    )
    assert picked == 1


async def test_ambiguous_same_title_both_plausible_rejected() -> None:
    """All Creatures Great and Small：新老两版都可信 → 歧义，进待识别。"""
    client = _client(
        {
            "/3/search/tv": {
                "results": [
                    _tv(101, "万物生灵", "All Creatures Great and Small", 1978),
                    _tv(102, "万物生灵", "All Creatures Great and Small", 2020),
                ]
            },
            "/3/tv/101": _tv_detail(101, seasons=7, episode_counts={4: 10}),
            "/3/tv/102": _tv_detail(102, seasons=5, episode_counts={4: 7}),
        }
    )
    picked = await verify_resolve(
        client,
        MediaKind.TV,
        LocalEvidence(title="All Creatures Great and Small", season=4, episode=1),
    )
    assert picked is None


async def test_translation_title_passes_gate() -> None:
    """外语片的英文名常只在 translations（alternative_titles 没有）：
    《自由的幻影》类场景，翻译名也要能过标题门槛。"""
    detail = _movie_detail(5558)
    detail["translations"] = {
        "translations": [{"data": {"title": "The Phantom of Liberty"}}]
    }
    client = _client(
        {
            "/3/search/movie": {
                "results": [_movie(5558, "自由的幻影", "Le fantôme de la liberté", 1974)]
            },
            "/3/movie/5558": detail,
        }
    )
    picked = await verify_resolve(
        client, MediaKind.MOVIE, LocalEvidence(title="The Phantom of Liberty", year=1974)
    )
    assert picked == 5558


async def test_year_supplementary_search_recovers_low_ranked() -> None:
    """Berserk 2016 类场景：正主在普通搜索中排位太低（前 5 之外），
    第一轮全灭后带年份参数补搜捞回，季数佐证命中。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/3/search/tv":
            if request.url.params.get("first_air_date_year") == "2016":
                return httpx.Response(
                    200, json={"results": [_tv(66053, "剑风传奇", "ベルセルク", 2016)]}
                )
            # 普通搜索只返回 1997 版（1 季，会被季数反证淘汰）
            return httpx.Response(
                200, json={"results": [_tv(409, "剑风传奇Berserk", "剣風伝奇ベルセルク", 1997)]}
            )
        details = {
            "/3/tv/409": _tv_detail(409, seasons=1, alts=["Berserk"]),
            "/3/tv/66053": _tv_detail(66053, seasons=2, episode_counts={2: 12}, alts=["Berserk"]),
        }
        payload = details.get(path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    client = TmdbClient(_KEY, transport=httpx.MockTransport(handler))
    picked = await verify_resolve(
        client, MediaKind.TV, LocalEvidence(title="Berserk", year=2016, season=2, episode=1)
    )
    assert picked == 66053


async def test_year_supplementary_needs_nontrivial_corroboration() -> None:
    """补搜池按年份过滤而来，年份佐证是循环论证：唯一候选若只有年份
    没有其他佐证 → 否决（Never Give Up → 龍昇不打烊 误配的回归）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/3/search/movie":
            if request.url.params.get("primary_release_year") == "2022":
                return httpx.Response(
                    200, json={"results": [_movie(1117775, "龍昇不打烊", "龍昇不打烊", 2022)]}
                )
            return httpx.Response(200, json={"results": []})
        details = {"/3/movie/1117775": _movie_detail(1117775, runtime=90, alts=["Never Give Up"])}
        payload = details.get(path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    client = TmdbClient(_KEY, transport=httpx.MockTransport(handler))
    picked = await verify_resolve(
        client, MediaKind.MOVIE, LocalEvidence(title="Never Give Up", year=2022)
    )
    assert picked is None


async def test_noise_query_no_results() -> None:
    """噪声词（漫画包/软件包）搜索无结果 → 直接放弃。"""
    client = _client({"/3/search/movie": {"results": []}})
    picked = await verify_resolve(
        client, MediaKind.MOVIE, LocalEvidence(title="DC Week+", year=2022)
    )
    assert picked is None


def test_parse_total_episodes() -> None:
    """副标题「全N集」解析：带空格/无空格都认，没有声明返回 None。"""
    assert parse_total_episodes("锵锵拾遗 全13集 类型: 纪录片") == 13
    assert parse_total_episodes("[超时空大玩家][全 32 集][国语中字]") == 32
    assert parse_total_episodes("爱拼会赢 国语中字") is None


async def test_alt_title_retry_recovers_pinyin_name() -> None:
    """Qiang Qiang Shi Yi 类场景：拼音查询词在 TMDB 无解，主词全灭后用
    副标题中文名（alt_title）换词重跑，标题门槛照常生效。"""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/3/search/tv":
            if request.url.params.get("query") == "锵锵拾遗":
                return httpx.Response(
                    200, json={"results": [_tv(291509, "锵锵拾遗", "锵锵拾遗", 2025)]}
                )
            return httpx.Response(200, json={"results": []})
        details = {"/3/tv/291509": _tv_detail(291509, seasons=1, episode_counts={1: 13})}
        payload = details.get(path)
        return httpx.Response(200 if payload else 404, json=payload or {})

    client = TmdbClient(_KEY, transport=httpx.MockTransport(handler))
    picked = await verify_resolve(
        client,
        MediaKind.TV,
        LocalEvidence(title="Qiang Qiang Shi Yi", alt_title="锵锵拾遗", total_episodes=13),
    )
    assert picked == 291509


async def test_total_episodes_disambiguates_same_title() -> None:
    """同名双版本、无年份无季集号：副标题「全13集」与 TMDB 第 1 季
    episode_count 精确相等者唯一 → 有佐证者胜出。"""
    client = _client(
        {
            "/3/search/tv": {
                "results": [
                    _tv(201, "山海情", "山海情", 2021),
                    _tv(202, "山海情", "山海情", 2022),
                ]
            },
            "/3/tv/201": _tv_detail(201, seasons=1, episode_counts={1: 13}),
            "/3/tv/202": _tv_detail(202, seasons=1, episode_counts={1: 24}),
        }
    )
    picked = await verify_resolve(
        client, MediaKind.TV, LocalEvidence(title="山海情", total_episodes=13)
    )
    assert picked == 201
