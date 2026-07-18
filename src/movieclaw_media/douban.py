"""豆瓣公开移动端榜单的最小异步客户端与发现页编排。

豆瓣视角只承担榜单浏览，不抓取完整详情。接口并非正式开放 API，因此请求保持
低频并使用长缓存；任一榜单失败只隐藏该行，避免影响 TMDB 视角。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from parsel import Selector

from movieclaw_cache import AsyncTTLCache, CacheStore, SwrCache
from movieclaw_media.models import (
    DiscoverPage,
    MediaCard,
    MediaDetail,
    MediaFacts,
    MediaKind,
    MediaRow,
    MediaSearchItem,
    MediaSource,
)

logger = logging.getLogger("movieclaw_media.douban")

DEFAULT_API_BASE_URL = "https://m.douban.com/rexxar/api/v2"
_PAGE_TTL = 6 * 60 * 60
_SEARCH_TTL = 10 * 60
_DETAIL_TTL = 6 * 60 * 60
_MIN_ROW_ITEMS = 4

# 持久缓存（L2）的双 TTL：新鲜期内不碰豆瓣；可用期内先返回旧值、后台刷新；
# 超出可用期才阻塞回源。榜单天然快变、详情基本不变，故档位差一个量级。
_COLLECTION_FRESH_TTL = 6 * 60 * 60
_COLLECTION_STALE_TTL = 3 * 24 * 60 * 60
_DETAIL_FRESH_TTL = 3 * 24 * 60 * 60
_DETAIL_STALE_TTL = 30 * 24 * 60 * 60
# 无效豆瓣 ID 的负缓存：防止坏 ID 被前端重试反复打豆瓣
_DETAIL_NEGATIVE_TTL = 60 * 60


class DoubanError(Exception):
    """豆瓣榜单请求失败；错误信息可直接展示给用户。"""


class DoubanClient:
    """只访问 subject_collection 榜单接口的低频 HTTP 客户端。"""

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_API_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        store: CacheStore | None = None,
    ) -> None:
        # 持久缓存缓存原始响应 JSON（而非解析后的模型）：上层解析逻辑迭代时
        # 无需清缓存。store 不注入时退化为无持久缓存的直连行为。
        self._swr = SwrCache(store, "douban")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                    "AppleWebKit/605.1.15"
                ),
                "Referer": "https://m.douban.com/movie/",
            },
            timeout=20,
            follow_redirects=True,
            transport=transport,
        )
        self._limiter = AsyncLimiter(1, 1)

    async def collection(self, collection_id: str, *, count: int = 30) -> dict[str, Any]:
        """读取一份榜单；不自动翻页，发现页一行只需要首批高排名条目。"""

        async def fetch() -> dict[str, Any]:
            try:
                async with self._limiter:
                    response = await self._client.get(
                        f"/subject_collection/{collection_id}/items",
                        params={"start": 0, "count": count, "items_only": 1, "for_mobile": 1},
                    )
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("豆瓣榜单请求失败：%s（%s）", collection_id, exc)
                raise DoubanError("访问豆瓣榜单失败，请稍后重试") from exc

        return await self._swr.get_or_fetch(
            f"collection:{collection_id}:{count}",
            fresh_ttl=_COLLECTION_FRESH_TTL,
            stale_ttl=_COLLECTION_STALE_TTL,
            factory=fetch,
        )

    async def search(self, keyword: str) -> list[dict[str, Any]]:
        """搜索豆瓣电影/剧集轻量候选，只解析移动搜索页明确提供的字段。"""
        try:
            async with self._limiter:
                response = await self._client.get(
                    "https://m.douban.com/search/",
                    params={"query": keyword, "type": "1002"},
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("豆瓣搜索请求失败：%s（%s）", keyword, exc)
            raise DoubanError("访问豆瓣搜索失败，请稍后重试") from exc

        selector = Selector(text=response.text)
        results: list[dict[str, Any]] = []
        for node in selector.css("ul.search_results_subjects > li"):
            href = node.css('a[href^="/movie/subject/"]::attr(href)').get("")
            match = re.search(r"/subject/(\d+)/", href)
            title = (node.css("span.subject-title::text").get("") or "").strip()
            poster = node.css("img::attr(src)").get("")
            rating_text = node.css("span.rating-stars::attr(data-rating)").get("")
            if not match or not title or not poster:
                continue
            rating = round(float(rating_text) / 10, 1) if rating_text else 0.0
            # 搜索页给的是较小的 s_ratio 图，同一路径切到 m_ratio 提升卡片清晰度。
            results.append(
                {
                    "id": match.group(1),
                    "title": title,
                    "rating": rating,
                    "poster_url": poster.replace("/s_ratio_poster/", "/m_ratio_poster/"),
                }
            )
        return results

    async def detail(self, douban_id: str) -> dict[str, Any]:
        """读取豆瓣移动详情；电影路径会由豆瓣自动重定向到正确的剧集路径。"""

        async def fetch() -> dict[str, Any] | None:
            try:
                async with self._limiter:
                    response = await self._client.get(
                        f"/movie/{douban_id}", params={"for_mobile": 1}
                    )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("豆瓣详情请求失败：%s（%s）", douban_id, exc)
                raise DoubanError("访问豆瓣详情失败，请稍后重试") from exc
            # 返回 None 触发负缓存：豆瓣确认无此条目与瞬时故障（抛异常）分开对待
            if not data.get("id") or not data.get("title"):
                return None
            return data

        data = await self._swr.get_or_fetch(
            f"detail:{douban_id}",
            fresh_ttl=_DETAIL_FRESH_TTL,
            stale_ttl=_DETAIL_STALE_TTL,
            negative_ttl=_DETAIL_NEGATIVE_TTL,
            factory=fetch,
        )
        if data is None:
            raise DoubanError("豆瓣未返回有效的条目详情")
        return data

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class _Collection:
    collection_id: str
    title: str
    ranked: bool = False
    count: int = 30


_MOVIE_COLLECTIONS = (
    _Collection("movie_real_time_hotest", "豆瓣实时热门电影", True),
    _Collection("movie_weekly_best", "豆瓣一周口碑电影榜", True),
    # 豆瓣接口支持单次返回完整 250 条；普通榜单仍只取首屏 30 条，避免无谓传输。
    _Collection("movie_top250", "豆瓣电影 Top 250", True, 250),
    _Collection("EC7Q5H2QI", "近期高分电影"),
)
_TV_COLLECTIONS = (
    _Collection("tv_chinese_best_weekly", "华语口碑剧集榜", True),
    _Collection("tv_global_best_weekly", "全球口碑剧集榜", True),
    _Collection("EC74443FY", "近期热门大陆剧"),
    _Collection("ECFA5DI7Q", "近期热门美剧"),
    _Collection("ECNA46YBA", "近期热门日剧"),
    _Collection("ECBE5CBEI", "近期热门韩剧"),
)


class DoubanDiscoverService:
    """把豆瓣榜单转换为项目统一的发现页模型，不提供条目详情。"""

    def __init__(self, client: DoubanClient) -> None:
        self._client = client
        self._cache = AsyncTTLCache()

    async def discover_page(self, kind: MediaKind) -> DiscoverPage:
        return await self._cache.get_or_set(
            f"douban-page:{kind.value}", _PAGE_TTL, lambda: self._build_page(kind)
        )

    async def search(self, keyword: str) -> list[MediaSearchItem]:
        """返回统一的轻量豆瓣搜索候选；相同关键词缓存十分钟。"""
        normalized = keyword.strip()

        async def load() -> list[MediaSearchItem]:
            results = await self._client.search(normalized)
            return [
                MediaSearchItem(source=MediaSource.DOUBAN, **result) for result in results
            ]

        return await self._cache.get_or_set(
            f"douban-search:{normalized.casefold()}", _SEARCH_TTL, load
        )

    async def media_detail(self, douban_id: str) -> MediaDetail:
        """读取并转换豆瓣详情；图片集和相似推荐缺失时保持空列表。"""
        return await self._cache.get_or_set(
            f"douban-detail:{douban_id}",
            _DETAIL_TTL,
            lambda: self._build_detail(douban_id),
        )

    async def _build_detail(self, douban_id: str) -> MediaDetail:
        data = await self._client.detail(douban_id)
        kind = MediaKind.TV if data.get("type") == "tv" or data.get("is_tv") else MediaKind.MOVIE
        year_text = str(data.get("year") or "")
        cover = data.get("cover_url") or (data.get("pic") or {}).get("large")
        if not year_text[:4].isdigit() or not cover:
            raise DoubanError("该豆瓣条目缺少年份或海报，暂时无法展示详情")
        rating = data.get("rating") or {}
        aliases = [str(alias) for alias in data.get("aka") or [] if alias]
        original_title = data.get("original_title") or next(
            (alias for alias in aliases if alias.isascii()), data["title"]
        )
        durations = data.get("durations") or []
        episodes = data.get("episodes_count") or data.get("webisode_count")
        extent = durations[0] if kind is MediaKind.MOVIE and durations else ""
        if kind is MediaKind.TV and episodes:
            extent = f"{episodes} 集"
        card = MediaCard(
            id=str(data["id"]),
            source=MediaSource.DOUBAN,
            type=kind,
            title=data["title"],
            original_title=original_title,
            year=int(year_text[:4]),
            rating=round(float(rating.get("value") or 0), 1),
            genres=[str(genre) for genre in data.get("genres") or []][:3],
            extent=extent,
            overview=(data.get("intro") or "").strip(),
            poster_url=cover,
        )
        directors = [person.get("name") for person in data.get("directors") or []]
        cast = [person.get("name") for person in data.get("actors") or []]
        pubdates = data.get("pubdate") or []
        released = data.get("release_date") or (pubdates[0] if pubdates else "")
        return MediaDetail(
            card=card,
            facts=MediaFacts(
                directors=[name for name in directors if name][:3],
                cast=[name for name in cast if name][:5],
                country=" / ".join(data.get("countries") or []),
                language=" / ".join(data.get("languages") or []),
                released=released,
                aliases=aliases,
                source_url=data.get("url") or data.get("sharing_url"),
            ),
        )

    async def _build_page(self, kind: MediaKind) -> DiscoverPage:
        specs = _MOVIE_COLLECTIONS if kind is MediaKind.MOVIE else _TV_COLLECTIONS
        results = await asyncio.gather(
            *(self._client.collection(spec.collection_id, count=spec.count) for spec in specs),
            return_exceptions=True,
        )
        rows: list[MediaRow] = []
        for spec, result in zip(specs, results, strict=True):
            if isinstance(result, BaseException):
                logger.warning("豆瓣发现行「%s」不可用：%s", spec.title, result)
                continue
            items = [
                card
                for raw in result.get("subject_collection_items", [])
                if (card := self._to_card(raw, kind)) is not None
            ]
            if len(items) >= _MIN_ROW_ITEMS:
                rows.append(
                    MediaRow(
                        id=f"douban-{spec.collection_id}",
                        title=spec.title,
                        ranked=spec.ranked,
                        items=items,
                    )
                )
        if not rows:
            first_error = next((r for r in results if isinstance(r, DoubanError)), None)
            if first_error:
                raise first_error
            raise DoubanError("豆瓣暂未返回可展示的榜单数据，请稍后重试")
        return DiscoverPage(hero=[], rows=rows)

    @staticmethod
    def _to_card(raw: dict[str, Any], kind: MediaKind) -> MediaCard | None:
        """豆瓣榜单条目映射；缺少 ID、标题、年份或海报的残缺条目不上墙。"""
        raw_type = raw.get("type")
        if raw_type and raw_type != kind.value:
            return None
        item_id = raw.get("id")
        title = raw.get("title") or ""
        subtitle = raw.get("card_subtitle") or ""
        year_text = str(raw.get("year") or subtitle.split(" / ", 1)[0])
        cover = raw.get("cover") or {}
        pic = raw.get("pic") or {}
        poster = cover.get("url") or pic.get("large") or raw.get("cover_url")
        if not item_id or not title or not year_text[:4].isdigit() or not poster:
            return None
        genres = raw.get("genres") or []
        if not genres:
            # 榜单的 card_subtitle/info 依次为年份、地区、类型、导演、主演；
            # 类型段内部以空格分隔，不能把导演和演员误当成类型标签。
            parts = subtitle.split(" / ")
            genre_text = parts[2] if len(parts) > 2 else ""
            genres = genre_text.split()
        photos = raw.get("photos") or []
        backdrop = photos[0] if photos else None
        if isinstance(backdrop, dict):
            backdrop = backdrop.get("large") or backdrop.get("url")
        rating = raw.get("rating") or {}
        return MediaCard(
            id=str(item_id), source=MediaSource.DOUBAN, type=kind, title=title,
            original_title=raw.get("original_title") or title, year=int(year_text[:4]),
            rating=round(float(rating.get("value") or 0), 1), genres=genres[:3],
            overview=(raw.get("description") or raw.get("info") or subtitle).strip(),
            poster_url=poster, backdrop_url=backdrop,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
