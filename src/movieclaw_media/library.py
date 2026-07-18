"""媒体身份层的纯数据获取与收敛逻辑（无数据库依赖）。

本模块是订阅功能"媒体条目"的 TMDB 侧实现（docs/design/subscription.md 第 1 节）：

- ``fetch_media_profile``：一次拉齐条目的身份信息（外部 ID、标题、别名集合、
  季集结构），产出与持久层解耦的 ``MediaProfile``；
- ``resolve_douban_to_tmdb``：豆瓣入口的收敛兜底通路（标题+年份搜索），
  命中 / 歧义 / 未找到三分支。

职责边界：本包不依赖 movieclaw_db，落库编排由 API 层的
``movieclaw_api.services.media_library`` 完成。
"""

from __future__ import annotations

import asyncio
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field

from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

# 别名收集范围：中文圈 + 英语圈的地区别名，加 zh/en 两种语言的译名。
# 种子命名以英文为主、副标题以中文为主，这两组覆盖了匹配内核的需要。
_ALIAS_REGIONS = frozenset({"CN", "HK", "TW", "SG", "US", "GB"})
_ALIAS_LANGUAGES = frozenset({"zh", "en"})

# 歧义时返回给前端确认弹层的候选数量上限
_MAX_CANDIDATES = 8


class EpisodeInfo(BaseModel):
    """单集信息；air_date 保持 ISO 字符串形态，与 media_season.episodes JSON 同构。"""

    episode_number: int
    name: str = ""
    air_date: str | None = Field(default=None, description="ISO 日期字符串；None=未定档")


class SeasonProfile(BaseModel):
    """一季的骨架：季级订阅与 wanted 生成所需的全部信息。"""

    season_number: int
    name: str = ""
    air_date: date | None = None
    episode_count: int | None = None
    episodes: list[EpisodeInfo] = Field(default_factory=list)


class MediaProfile(BaseModel):
    """条目身份信息的传输模型：TMDB 原始数据 → 持久层字段的中间形态。"""

    kind: MediaKind
    tmdb_id: int
    imdb_id: str | None = None
    title: str
    original_title: str
    year: int | None = None
    aliases: list[str] = Field(default_factory=list)
    status: str | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    seasons: list[SeasonProfile] = Field(default_factory=list, description="仅剧集非空")


async def fetch_media_profile(
    client: TmdbClient,
    kind: MediaKind,
    tmdb_id: int,
    *,
    language: str = "zh-CN",
) -> MediaProfile:
    """拉取条目的完整身份信息（一次详情请求 + 剧集逐季并发拉集列表）。

    append_to_response 把别名/译名/外部 ID 合并进详情请求，整个电影建档只需
    一次往返；剧集另按季数并发拉集列表（受客户端漏桶限流约束）。
    """
    data = await client.get(
        f"{kind.value}/{tmdb_id}",
        {
            "language": language,
            "append_to_response": "alternative_titles,translations,external_ids",
        },
    )

    title = data.get("title") or data.get("name") or ""
    original_title = data.get("original_title") or data.get("original_name") or title
    release_date = data.get("release_date") or data.get("first_air_date") or ""

    seasons: list[SeasonProfile] = []
    if kind is MediaKind.TV:
        numbers = [
            s["season_number"]
            for s in data.get("seasons", [])
            if s.get("season_number") is not None
        ]
        seasons = list(
            await asyncio.gather(
                *(_fetch_season(client, tmdb_id, n, language) for n in numbers)
            )
        )

    return MediaProfile(
        kind=kind,
        tmdb_id=tmdb_id,
        imdb_id=(data.get("external_ids") or {}).get("imdb_id") or None,
        title=title,
        original_title=original_title,
        year=_parse_year(release_date),
        aliases=_build_aliases(data, title, original_title),
        status=data.get("status") or None,
        poster_path=data.get("poster_path"),
        backdrop_path=data.get("backdrop_path"),
        seasons=seasons,
    )


async def _fetch_season(
    client: TmdbClient, tmdb_id: int, season_number: int, language: str
) -> SeasonProfile:
    data = await client.get(f"tv/{tmdb_id}/season/{season_number}", {"language": language})
    episodes = [
        EpisodeInfo(
            episode_number=e["episode_number"],
            name=e.get("name") or "",
            air_date=e.get("air_date") or None,
        )
        for e in data.get("episodes", [])
        if e.get("episode_number") is not None
    ]
    return SeasonProfile(
        season_number=season_number,
        name=data.get("name") or "",
        air_date=_parse_date(data.get("air_date") or ""),
        # 详情季对象可能带 episode_count；集列表已拉到时以实际长度为准
        episode_count=len(episodes) or data.get("episode_count"),
        episodes=episodes,
    )


def _build_aliases(data: dict, title: str, original_title: str) -> list[str]:
    """构建别名集合：主标题 + 原名 + 地区别名 + zh/en 译名，保序精确去重。

    存原样文本——归一化（大小写/全半角/繁简）是匹配内核的职责，
    规则进化时数据无需重写。
    """
    collected: list[str] = [title, original_title]

    alt = data.get("alternative_titles") or {}
    # TMDB 的接口差异：电影用 "titles" 键，剧集用 "results" 键
    for entry in alt.get("titles") or alt.get("results") or []:
        if entry.get("iso_3166_1") in _ALIAS_REGIONS:
            collected.append(entry.get("title") or "")

    for trans in (data.get("translations") or {}).get("translations") or []:
        if trans.get("iso_639_1") in _ALIAS_LANGUAGES:
            payload = trans.get("data") or {}
            collected.append(payload.get("title") or payload.get("name") or "")

    seen: set[str] = set()
    aliases: list[str] = []
    for text in collected:
        cleaned = text.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            aliases.append(cleaned)
    return aliases


def _parse_year(iso_date: str) -> int | None:
    if len(iso_date) >= 4 and iso_date[:4].isdigit():
        return int(iso_date[:4])
    return None


def _parse_date(iso_date: str) -> date | None:
    try:
        return date.fromisoformat(iso_date)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 豆瓣入口收敛：标题+年份搜索兜底（设计稿 1.5 的②通路）
# ---------------------------------------------------------------------------


class ResolveStatus(StrEnum):
    """收敛结果三分支。"""

    MATCHED = "matched"  # 唯一命中，可直接建档
    AMBIGUOUS = "ambiguous"  # 多候选，需用户在弹层确认一次
    NOT_FOUND = "not_found"  # TMDB 未收录，不建无锚条目


class ResolveCandidate(BaseModel):
    """返回给确认弹层的候选条目。"""

    tmdb_id: int
    title: str
    original_title: str
    year: int | None = None
    poster_path: str | None = None


class DoubanResolution(BaseModel):
    """豆瓣→TMDB 收敛结果。matched 时 tmdb_id 非空；ambiguous 时 candidates 非空。"""

    status: ResolveStatus
    tmdb_id: int | None = None
    candidates: list[ResolveCandidate] = Field(default_factory=list)


async def resolve_douban_to_tmdb(
    client: TmdbClient,
    kind: MediaKind,
    title: str,
    *,
    year: int | None = None,
    language: str = "zh-CN",
) -> DoubanResolution:
    """按标题+年份把豆瓣条目收敛到 TMDB 锚。

    判定规则（保守优先，绝不静默错配）：
    1. 年份过滤（容差 ±1，豆瓣与 TMDB 偶有跨年差异）后唯一 → 命中；
    2. 过滤后多个，但"标题精确相等且年份精确相等"者唯一 → 命中；
    3. 其余 → 歧义，返回候选让用户确认；搜索结果为空 → 未找到。
    """
    data = await client.get(f"search/{kind.value}", {"query": title, "language": language})
    candidates = [_to_candidate(raw) for raw in data.get("results", [])]
    candidates = [c for c in candidates if c is not None]
    if not candidates:
        return DoubanResolution(status=ResolveStatus.NOT_FOUND)

    pool = candidates
    if year is not None:
        filtered = [c for c in candidates if c.year is not None and abs(c.year - year) <= 1]
        # 年份全部对不上时退回全量候选——豆瓣年份可能就是错的，交给用户判断
        pool = filtered or candidates

    if len(pool) == 1:
        return DoubanResolution(status=ResolveStatus.MATCHED, tmdb_id=pool[0].tmdb_id)

    if year is not None:
        wanted = _loose(title)
        exact = [
            c
            for c in pool
            if c.year == year and wanted in (_loose(c.title), _loose(c.original_title))
        ]
        if len(exact) == 1:
            return DoubanResolution(status=ResolveStatus.MATCHED, tmdb_id=exact[0].tmdb_id)

    return DoubanResolution(status=ResolveStatus.AMBIGUOUS, candidates=pool[:_MAX_CANDIDATES])


def _to_candidate(raw: dict) -> ResolveCandidate | None:
    tmdb_id = raw.get("id")
    title = raw.get("title") or raw.get("name") or ""
    if not tmdb_id or not title:
        return None
    return ResolveCandidate(
        tmdb_id=tmdb_id,
        title=title,
        original_title=raw.get("original_title") or raw.get("original_name") or title,
        year=_parse_year(raw.get("release_date") or raw.get("first_air_date") or ""),
        poster_path=raw.get("poster_path"),
    )


def _loose(text: str) -> str:
    """仅用于"精确相等"判定的宽松形态：忽略大小写与空白差异。"""
    return "".join(text.split()).casefold()
