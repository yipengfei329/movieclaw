"""发现页数据编排：把 TMDB 的多个榜单接口聚合成前端一屏可渲染的页面。

页面构成（Netflix 式）
--------------------
- hero：本周趋势榜里挑「有宽幅剧照且有中文简介」的前几名做大横幅轮播；
- rows：每行对应一个 TMDB 榜单/发现查询（行定义见 _movie_rows / _tv_rows），
  全部并发拉取；单行失败只丢那一行、不拖垮整页（对齐站点搜索的错误隔离
  口径），全部失败才向上抛错。

缓存口径
--------
页面 30 分钟、类型表 24 小时、详情 6 小时——都是全站共享的只读榜单数据，
无个性化成分，进程内 TTL 缓存即可（见 cache.py 的说明）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from movieclaw_cache import AsyncTTLCache
from movieclaw_media.models import (
    DiscoverPage,
    MediaCard,
    MediaDetail,
    MediaFacts,
    MediaImage,
    MediaKind,
    MediaRow,
    MediaSearchItem,
    MediaSource,
)
from movieclaw_media.tmdb import TmdbClient, TmdbError

logger = logging.getLogger("movieclaw_media.service")

_PAGE_TTL = 30 * 60
_GENRE_TTL = 24 * 60 * 60
_DETAIL_TTL = 6 * 60 * 60
_SEARCH_TTL = 10 * 60
# Hero 轮播的精选数量
_HERO_COUNT = 6
# 一行少于这个数就不值得占一行位置（如所选地区暂无「正在热映」数据）
_MIN_ROW_ITEMS = 4
# 相似推荐最多取多少部
_RELATED_LIMIT = 10
# 详情页剧照/海报各取多少张（TMDB 已按社区评分排好序，取前 N 即最佳 N 张）
_IMAGES_LIMIT = 20

# TMDB 详情接口不随 language 参数翻译制片地区/语言字段，这里映射高频值，
# 映射不到的原样展示（英文/ISO 代码），不影响功能
_COUNTRY_NAMES = {
    "US": "美国", "CN": "中国大陆", "HK": "中国香港", "TW": "中国台湾",
    "JP": "日本", "KR": "韩国", "GB": "英国", "FR": "法国", "DE": "德国",
    "IT": "意大利", "ES": "西班牙", "CA": "加拿大", "AU": "澳大利亚",
    "IN": "印度", "TH": "泰国", "RU": "俄罗斯", "NZ": "新西兰",
    "IE": "爱尔兰", "DK": "丹麦", "SE": "瑞典", "NO": "挪威",
    "BE": "比利时", "NL": "荷兰", "BR": "巴西", "MX": "墨西哥",
}
_LANGUAGE_NAMES = {
    "en": "英语", "zh": "汉语普通话", "cn": "粤语", "ja": "日语", "ko": "韩语",
    "fr": "法语", "de": "德语", "es": "西班牙语", "it": "意大利语",
    "ru": "俄语", "pt": "葡萄牙语", "hi": "印地语", "th": "泰语",
    "sv": "瑞典语", "da": "丹麦语", "no": "挪威语", "nl": "荷兰语",
    "pl": "波兰语", "tr": "土耳其语",
}


@dataclass(frozen=True)
class _RowSpec:
    """一行榜单的声明式定义：行标识/中文标题/TMDB 端点/查询参数。"""

    row_id: str
    title: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    ranked: bool = False
    limit: int = 20


def _movie_rows(region: str) -> tuple[_RowSpec, ...]:
    return (
        _RowSpec("trending-day", "今日热榜 Top 10", "trending/movie/day", ranked=True, limit=10),
        _RowSpec("popular", "热门电影", "movie/popular"),
        _RowSpec("now-playing", "正在热映", "movie/now_playing", {"region": region}),
        _RowSpec("top-rated", "高分经典", "movie/top_rated"),
        _RowSpec("upcoming", "即将上映", "movie/upcoming", {"region": region}),
        _RowSpec(
            "chinese", "华语佳片", "discover/movie",
            {"with_original_language": "zh", "sort_by": "popularity.desc", "vote_count.gte": 50},
        ),
        _RowSpec(
            "scifi", "科幻巨制", "discover/movie",
            {"with_genres": "878", "sort_by": "popularity.desc", "vote_count.gte": 300},
        ),
        _RowSpec(
            "animation", "动画电影", "discover/movie",
            {"with_genres": "16", "sort_by": "popularity.desc", "vote_count.gte": 200},
        ),
    )


def _tv_rows() -> tuple[_RowSpec, ...]:
    return (
        _RowSpec("trending-day", "今日热榜 Top 10", "trending/tv/day", ranked=True, limit=10),
        _RowSpec("popular", "热门剧集", "tv/popular"),
        _RowSpec("on-the-air", "正在播出", "tv/on_the_air"),
        _RowSpec("top-rated", "高分神剧", "tv/top_rated"),
        _RowSpec(
            "chinese", "华语剧集", "discover/tv",
            {"with_original_language": "zh", "sort_by": "popularity.desc", "vote_count.gte": 20},
        ),
        _RowSpec(
            "scifi-fantasy", "科幻与奇幻", "discover/tv",
            {"with_genres": "10765", "sort_by": "popularity.desc", "vote_count.gte": 100},
        ),
        _RowSpec(
            "animation", "动画剧集", "discover/tv",
            {"with_genres": "16", "sort_by": "popularity.desc", "vote_count.gte": 100},
        ),
    )


class MediaDiscoverService:
    """发现页/详情的编排服务：TMDB 原始数据 → 前端可渲染的模型。"""

    def __init__(
        self,
        client: TmdbClient,
        *,
        image_base_url: str,
        language: str = "zh-CN",
        region: str = "CN",
    ) -> None:
        self._client = client
        self._image_base = image_base_url.rstrip("/")
        self._language = language
        self._region = region
        self._cache = AsyncTTLCache()

    # ------------------------------------------------------------------
    # 发现页
    # ------------------------------------------------------------------

    async def discover_page(self, kind: MediaKind) -> DiscoverPage:
        return await self._cache.get_or_set(
            f"page:{kind.value}", _PAGE_TTL, lambda: self._build_page(kind)
        )

    async def _build_page(self, kind: MediaKind) -> DiscoverPage:
        genre_map = await self._genre_map(kind)
        specs = _movie_rows(self._region) if kind is MediaKind.MOVIE else _tv_rows()

        results = await asyncio.gather(
            self._fetch_cards(f"trending/{kind.value}/week", {}, kind, genre_map),
            *(self._fetch_cards(s.path, s.params, kind, genre_map) for s in specs),
            return_exceptions=True,
        )
        hero_result, row_results = results[0], results[1:]

        hero: list[MediaCard] = []
        if isinstance(hero_result, BaseException):
            logger.warning("发现页 Hero 数据拉取失败：%s", hero_result)
        else:
            # Hero 只收「有宽幅剧照且有简介」的条目，保证大横幅视觉与文案完整
            hero = [c for c in hero_result if c.backdrop_url and c.overview][:_HERO_COUNT]

        rows: list[MediaRow] = []
        for spec, result in zip(specs, row_results):
            if isinstance(result, BaseException):
                logger.warning("发现页榜单「%s」拉取失败：%s", spec.title, result)
                continue
            items = result[: spec.limit]
            if len(items) < _MIN_ROW_ITEMS:
                continue
            rows.append(MediaRow(id=spec.row_id, title=spec.title, ranked=spec.ranked, items=items))

        if not rows and not hero:
            # 整页全军覆没：把第一个真实错误抛给用户（通常是 Key 无效/网络不通）
            first_error = next((r for r in results if isinstance(r, BaseException)), None)
            if isinstance(first_error, TmdbError):
                raise first_error
            raise TmdbError("TMDB 未返回任何可用数据，请稍后重试")
        return DiscoverPage(hero=hero, rows=rows)

    async def _fetch_cards(
        self, path: str, params: dict[str, Any], kind: MediaKind, genre_map: dict[int, str]
    ) -> list[MediaCard]:
        data = await self._client.get(path, {"language": self._language, **params})
        cards = (self._to_card(raw, kind, genre_map) for raw in data.get("results", []))
        return [c for c in cards if c is not None]

    def _to_card(
        self, raw: dict[str, Any], kind: MediaKind, genre_map: dict[int, str]
    ) -> MediaCard | None:
        """TMDB 列表条目 → 海报卡片；缺海报/标题/年份的条目没法上墙，返回 None 剔除。"""
        title = raw.get("title") or raw.get("name") or ""
        date = raw.get("release_date") or raw.get("first_air_date") or ""
        poster = raw.get("poster_path")
        if not poster or not title or len(date) < 4 or not date[:4].isdigit():
            return None
        # 详情接口给的是 genres 对象列表，列表接口给的是 genre_ids，两者兼容
        genre_ids = raw.get("genre_ids") or [g["id"] for g in raw.get("genres", [])]
        backdrop = raw.get("backdrop_path")
        return MediaCard(
            id=str(raw["id"]),
            type=kind,
            title=title,
            original_title=raw.get("original_title") or raw.get("original_name") or title,
            year=int(date[:4]),
            rating=round(float(raw.get("vote_average") or 0.0), 1),
            genres=[genre_map[g] for g in genre_ids if g in genre_map][:3],
            overview=(raw.get("overview") or "").strip(),
            poster_url=f"{self._image_base}/w500{poster}",
            backdrop_url=f"{self._image_base}/w1280{backdrop}" if backdrop else None,
        )

    async def _genre_map(self, kind: MediaKind) -> dict[int, str]:
        """TMDB 类型 ID → 中文名（如 878 → 科幻）。全站共享，缓存 24 小时。"""

        async def load() -> dict[int, str]:
            data = await self._client.get(
                f"genre/{kind.value}/list", {"language": self._language}
            )
            return {g["id"]: g["name"] for g in data.get("genres", [])}

        return await self._cache.get_or_set(f"genres:{kind.value}", _GENRE_TTL, load)

    # ------------------------------------------------------------------
    # 搜索
    # ------------------------------------------------------------------

    async def search(self, keyword: str) -> list[MediaSearchItem]:
        """TMDB multi 搜索 → 轻量候选（自带年份和 movie/tv 类型，豆瓣搜索没有）。

        用 search/multi 而非分别搜电影/剧集：一次请求，且两类条目按 TMDB
        的全局热度统一排序，不需要自己拼接排序。相同关键词缓存十分钟，
        口径与豆瓣搜索一致。
        """
        normalized = keyword.strip()

        async def load() -> list[MediaSearchItem]:
            data = await self._client.get(
                "search/multi", {"language": self._language, "query": normalized}
            )
            items = (self._to_search_item(raw) for raw in data.get("results", []))
            return [item for item in items if item is not None]

        return await self._cache.get_or_set(
            f"search:{normalized.casefold()}", _SEARCH_TTL, load
        )

    def _to_search_item(self, raw: dict[str, Any]) -> MediaSearchItem | None:
        """multi 搜索条目 → 轻量候选；人物条目和缺海报/标题的条目剔除。"""
        media_type = raw.get("media_type")
        if media_type not in (MediaKind.MOVIE.value, MediaKind.TV.value):
            return None
        title = raw.get("title") or raw.get("name") or ""
        poster = raw.get("poster_path")
        if not title or not poster:
            return None
        date = raw.get("release_date") or raw.get("first_air_date") or ""
        return MediaSearchItem(
            id=str(raw["id"]),
            source=MediaSource.TMDB,
            title=title,
            year=int(date[:4]) if date[:4].isdigit() else None,
            type=MediaKind(media_type),
            rating=round(float(raw.get("vote_average") or 0), 1),
            poster_url=f"{self._image_base}/w342{poster}",
        )

    # ------------------------------------------------------------------
    # 条目详情
    # ------------------------------------------------------------------

    async def media_detail(self, kind: MediaKind, tmdb_id: int) -> MediaDetail:
        return await self._cache.get_or_set(
            f"detail:{kind.value}:{tmdb_id}",
            _DETAIL_TTL,
            lambda: self._build_detail(kind, tmdb_id),
        )

    async def _build_detail(self, kind: MediaKind, tmdb_id: int) -> MediaDetail:
        genre_map = await self._genre_map(kind)
        # append_to_response：演职员/相似推荐/图片集随详情一次请求带回，省三次往返。
        # include_image_language 必须显式给：默认按 language 过滤会把图滤到几乎没有
        # ——剧照大多不带语言标注（null），海报则按语言分版本。
        primary_language = self._language.split("-")[0]
        data = await self._client.get(
            f"{kind.value}/{tmdb_id}",
            {
                "language": self._language,
                "append_to_response": "credits,recommendations,images",
                "include_image_language": f"{primary_language},en,null",
            },
        )
        card = self._to_card(data, kind, genre_map)
        if card is None:
            raise TmdbError("该条目在 TMDB 中缺少海报或标题等必要信息，无法展示")
        card.extent = self._extent(data, kind)

        related_raw = (data.get("recommendations") or {}).get("results", [])
        related = [
            c
            for c in (self._to_card(r, kind, genre_map) for r in related_raw)
            if c is not None
        ][:_RELATED_LIMIT]
        backdrops, posters = self._images(data)
        return MediaDetail(
            card=card,
            facts=self._facts(data, kind),
            backdrops=backdrops,
            posters=posters,
            related=related,
        )

    def _images(self, data: dict[str, Any]) -> tuple[list[MediaImage], list[MediaImage]]:
        """详情里的图片集 → 剧照/海报列表（各取评分最高的前 N 张）。

        海报把配置语言（如中文版）排到最前——用户点开更可能想要本地化海报；
        剧照无语言之分，保持 TMDB 的评分排序。
        """
        images = data.get("images") or {}
        primary_language = self._language.split("-")[0]

        def build(raw: dict[str, Any], preview_size: str) -> MediaImage | None:
            path = raw.get("file_path")
            if not path:
                return None
            return MediaImage(
                preview_url=f"{self._image_base}/{preview_size}{path}",
                full_url=f"{self._image_base}/original{path}",
                width=raw.get("width") or 0,
                height=raw.get("height") or 0,
            )

        backdrops = [
            img
            for img in (build(r, "w780") for r in images.get("backdrops", []))
            if img is not None
        ][:_IMAGES_LIMIT]

        posters_raw = sorted(
            images.get("posters", []),
            key=lambda r: r.get("iso_639_1") != primary_language,  # 配置语言在前，组内保持原序
        )
        posters = [
            img
            for img in (build(r, "w342") for r in posters_raw)
            if img is not None
        ][:_IMAGES_LIMIT]
        return backdrops, posters

    @staticmethod
    def _extent(data: dict[str, Any], kind: MediaKind) -> str:
        if kind is MediaKind.MOVIE:
            runtime = data.get("runtime")
            return f"{runtime} 分钟" if runtime else ""
        seasons = data.get("number_of_seasons")
        return f"{seasons} 季" if seasons else ""

    def _facts(self, data: dict[str, Any], kind: MediaKind) -> MediaFacts:
        credits = data.get("credits") or {}
        if kind is MediaKind.MOVIE:
            directors = [c["name"] for c in credits.get("crew", []) if c.get("job") == "Director"]
            country_codes = [c.get("iso_3166_1", "") for c in data.get("production_countries", [])]
        else:
            directors = [c["name"] for c in data.get("created_by", [])]
            country_codes = data.get("origin_country") or [
                c.get("iso_3166_1", "") for c in data.get("production_countries", [])
            ]

        networks = data.get("networks") or []
        original_language = data.get("original_language") or ""
        return MediaFacts(
            directors=directors[:3],
            cast=[c["name"] for c in credits.get("cast", [])[:5]],
            country=" / ".join(_COUNTRY_NAMES.get(c, c) for c in country_codes if c),
            language=_LANGUAGE_NAMES.get(original_language, original_language),
            released=data.get("release_date") or data.get("first_air_date") or "",
            network=networks[0].get("name") if kind is MediaKind.TV and networks else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
