"""M-Team（馒头）站点适配器。

与其他 NexusPHP 站点不同，M-Team 提供了完整的 REST API，因此本适配器
直接调用官方 JSON 接口，而非解析 HTML 页面。核心接口（均为 POST）：

- ``/api/torrent/search``       搜索/浏览种子（分类、关键词、免费过滤、成人模式）
- ``/api/torrent/detail``       种子详情
- ``/api/torrent/genDlToken``   换取一次性下载直链，再 GET 下载 .torrent
- ``/api/member/profile``       当前用户资料（用户名、上传下载量、分享率等）
- ``/api/tracker/myPeerStatus`` 当前做种/下载计数

认证方式
--------
优先使用 API-Key（请求头 ``x-api-key``，见 ``ApiKeyAuthProvider``）。
在站点网页「控制台 → 实验室 → 密钥」创建后填入即可。

设计要点
--------
1. M-Team 把「成人内容(AV)」放在独立的 ``mode=adult`` 索引里，与普通内容
   （``mode=normal``）不在同一次查询中返回。因此当请求同时包含 AV 和非 AV
   分类时，需要分别请求两次再合并（与原 moviebot 实现保持一致）。
2. 站点分类 ID → 应用级 TorrentCategory 的映射来自 YAML 的 ``categories``，
   由注册表解析后通过构造器注入，本类据此构建正向/反向两张映射表。
3. 促销力度以字符串枚举返回（NORMAL/FREE/PERCENT_70/PERCENT_50），
   在 ``_DISCOUNT_MAP`` 中转换为下载系数。
"""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any

from movieclaw_tracker.base import BaseSite
from movieclaw_tracker.exceptions import TrackerParseError
from movieclaw_tracker.models import (
    SearchQuery,
    SearchResult,
    TorrentCategory,
    TorrentDetail,
    TorrentListItem,
    TorrentListPage,
    UserProfile,
)

logger = logging.getLogger("movieclaw_tracker.sites.mteam")

# API 返回的时间格式统一为 "2026-01-02 15:04:05"
_DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

# 促销力度枚举 → 下载系数（download_volume_factor）
# FREE 全免、PERCENT_70 表示只计 30% 下载量、PERCENT_50 计 50%
_DISCOUNT_MAP: dict[str, float] = {
    "NORMAL": 1.0,
    "FREE": 0.0,
    "PERCENT_70": 0.3,
    "PERCENT_50": 0.5,
    "_2X_FREE": 0.0,  # 双倍上传且免费下载
    "_2X_PERCENT_50": 0.5,
    "_2X": 1.0,
}

# 上传系数：带 _2X 前缀的促销为双倍上传
_UPLOAD_2X_DISCOUNTS = {"_2X", "_2X_FREE", "_2X_PERCENT_50"}

# 每页请求数量，与原实现一致取 100
_PAGE_SIZE = 100

# 种子列表接口默认拉取的一级分类（等价于「浏览全部非成人内容」）
_DEFAULT_LIST_CATEGORIES = [
    TorrentCategory.MOVIE,
    TorrentCategory.TV,
    TorrentCategory.DOCUMENTARY,
    TorrentCategory.ANIME,
    TorrentCategory.MUSIC,
    TorrentCategory.GAME,
    TorrentCategory.OTHER,
]

_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB", "PB"]


def _bytes_to_human(size: int) -> str:
    """把字节数转成可读字符串（如 ``25.6 GB``），仅用于展示。"""
    value = float(size)
    for unit in _SIZE_UNITS:
        if value < 1024 or unit == _SIZE_UNITS[-1]:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} PB"


class MTeamSite(BaseSite):
    """M-Team 站点，基于官方 REST API 实现。"""

    def __init__(
        self,
        *,
        site_id: str,
        base_url: str,
        client: Any,
        auth_manager: Any,
        web_base_url: str | None = None,
        category_map: dict[TorrentCategory, list[str]] | None = None,
    ) -> None:
        super().__init__(
            site_id=site_id,
            base_url=base_url,
            client=client,
            auth_manager=auth_manager,
            web_base_url=web_base_url,
        )
        # 正向映射：应用级一级分类 → 站点分类 ID 列表（用于搜索时下发过滤条件）
        self._category_map: dict[TorrentCategory, list[str]] = category_map or {}
        # 反向映射：站点分类 ID → 应用级一级分类（用于解析结果时归类）
        self._reverse_map: dict[str, TorrentCategory] = {}
        for cate, ids in self._category_map.items():
            for cid in ids:
                self._reverse_map[str(cid)] = cate

    # -- 内部：API 调用与结果校验 ------------------------------------------

    async def _post_api(self, path: str, **kwargs: Any) -> Any:
        """POST 调用 M-Team API，校验业务状态后返回 ``data`` 字段。

        M-Team 统一响应结构为 ``{"code": "0", "message": "SUCCESS", "data": ...}``。
        HTTP 200 但 message 非 SUCCESS 时同样视为失败（如 Key 失效、参数错误）。
        """
        url = self._url(path)
        response = await self.client.post(url, **kwargs)
        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001 - 统一转成业务异常，便于上层展示
            raise TrackerParseError(
                "M-Team 接口返回内容不是合法 JSON，站点可能维护中或 API 已变更",
                details={"url": url},
            ) from exc

        message = str(payload.get("message", "")).upper()
        if message not in ("SUCCESS", "OK", "0", ""):
            # 常见原因：API-Key 失效、未认证、触发风控
            raise TrackerParseError(
                f"M-Team 接口返回错误：{payload.get('message')}",
                details={"url": url, "code": payload.get("code")},
            )
        return payload.get("data")

    # -- 分类工具 ----------------------------------------------------------

    def _collect_category_ids(self, categories: list[TorrentCategory]) -> list[str]:
        """把请求的一级分类展开成站点分类 ID 列表。"""
        ids: list[str] = []
        for cate in categories:
            ids.extend(self._category_map.get(cate, []))
        return ids

    def _resolve_category(self, site_cate_id: str | None) -> TorrentCategory | None:
        """站点分类 ID → 应用级一级分类。"""
        if site_cate_id is None:
            return None
        return self._reverse_map.get(str(site_cate_id))

    # -- 结果解析 ----------------------------------------------------------

    def _parse_datetime(self, text: str | None) -> datetime.datetime | None:
        if not text:
            return None
        try:
            return datetime.datetime.strptime(text, _DATETIME_FMT)
        except (ValueError, TypeError):
            return None

    def _parse_torrent(self, t: dict[str, Any]) -> TorrentListItem | None:
        """把单条 API 种子数据映射为 TorrentListItem。解析异常返回 None 跳过。"""
        try:
            status = t.get("status") or {}
            torrent_id = str(t.get("id", ""))
            site_cate_id = str(t.get("category")) if t.get("category") is not None else None

            # 促销力度 → 下载/上传系数
            discount = status.get("discount", "NORMAL")
            download_factor = _DISCOUNT_MAP.get(discount, 1.0)
            upload_factor = 2.0 if discount in _UPLOAD_2X_DISCOUNTS else 1.0

            # 促销截止时间：优先用接口给的精确时间；无时间但确有折扣则视为长期有效
            free_deadline = self._parse_datetime(status.get("discountEndTime"))
            if free_deadline is None and download_factor < 1.0:
                free_deadline = datetime.datetime.max

            # 大小：M-Team 以字节字符串返回
            size_bytes = int(t.get("size") or 0)

            image_list = [str(u) for u in (t.get("imageList") or []) if u]
            poster_url = image_list[0] if image_list else None

            return TorrentListItem(
                torrent_id=torrent_id,
                title=t.get("name") or "",
                subtitle=t.get("smallDescr") or "",
                category=self._resolve_category(site_cate_id),
                site_category_id=site_cate_id,
                size=_bytes_to_human(size_bytes) if size_bytes else None,
                size_bytes=size_bytes,
                seeders=int(status.get("seeders", 0) or 0),
                leechers=int(status.get("leechers", 0) or 0),
                snatched=int(status.get("timesCompleted", 0) or 0),
                upload_time=self._parse_datetime(t.get("createdDate")),
                poster_url=poster_url,
                image_urls=image_list,
                free=download_factor == 0.0,
                free_deadline=free_deadline,
                download_volume_factor=download_factor,
                upload_volume_factor=upload_factor,
                # download_url 存种子 ID，download_torrent 据此换取下载直链
                download_url=torrent_id,
                # 详情链接给用户在浏览器打开，必须用网页域名而非 API 域名
                detail_url=f"{self.web_base_url}/detail/{torrent_id}",
            )
        except Exception:  # noqa: BLE001 - 单条解析失败不应中断整页
            logger.warning("解析 M-Team 种子数据失败，已跳过该条", exc_info=True)
            return None

    # -- 单次搜索请求 ------------------------------------------------------

    async def _search_once(
        self,
        *,
        keyword: str | None,
        category_ids: list[str],
        page: int,
        adult: bool,
        free: bool,
    ) -> tuple[list[TorrentListItem], int | None]:
        """执行一次 search 请求，返回（种子列表，总页数）。"""
        params: dict[str, Any] = {
            "categories": category_ids,
            "keyword": keyword or "",
            "pageNumber": page,
            "pageSize": _PAGE_SIZE,
            "visible": 1,
        }
        if adult:
            params["mode"] = "adult"
        if free:
            params["discount"] = "FREE"

        data = await self._post_api("/api/torrent/search", json=params)
        if not data:
            return [], None

        raw_list = data.get("data") or []
        items = [item for t in raw_list if (item := self._parse_torrent(t)) is not None]

        total_pages = data.get("totalPages")
        try:
            total_pages = int(total_pages) if total_pages is not None else None
        except (ValueError, TypeError):
            total_pages = None

        return items, total_pages

    async def _search(
        self,
        *,
        keyword: str | None,
        categories: list[TorrentCategory] | None,
        page: int,
        free: bool = False,
    ) -> tuple[list[TorrentListItem], int | None]:
        """统一的搜索入口，负责处理成人/非成人两个索引的拆分与合并。"""
        requested = set(categories) if categories else set(TorrentCategory)
        want_adult = TorrentCategory.AV in requested
        normal_cats = [c for c in requested if c != TorrentCategory.AV]

        items: list[TorrentListItem] = []
        total_pages: int | None = None

        # 非成人内容
        if normal_cats:
            # 若涵盖全部非成人分类，则下发空列表表示「不按分类过滤」
            all_normal = set(normal_cats) >= set(_DEFAULT_LIST_CATEGORIES)
            cate_ids = [] if all_normal else self._collect_category_ids(normal_cats)
            normal_items, total_pages = await self._search_once(
                keyword=keyword,
                category_ids=cate_ids,
                page=page,
                adult=False,
                free=free,
            )
            items.extend(normal_items)

        # 成人内容（独立索引，mode=adult）
        if want_adult:
            adult_ids = self._collect_category_ids([TorrentCategory.AV])
            adult_items, adult_pages = await self._search_once(
                keyword=keyword,
                category_ids=adult_ids,
                page=page,
                adult=True,
                free=free,
            )
            items.extend(adult_items)
            if total_pages is None:
                total_pages = adult_pages

        return items, total_pages

    # -- BaseSite 契约实现 -------------------------------------------------

    async def list_torrents(
        self,
        *,
        categories: list[TorrentCategory] | None = None,
        page: int = 1,
    ) -> TorrentListPage:
        # 未指定分类时，默认浏览全部（含成人内容），与原实现的 list() 行为一致
        cats = categories if categories is not None else list(TorrentCategory)
        items, total_pages = await self._search(
            keyword=None,
            categories=cats,
            page=page,
        )
        return TorrentListPage(items=items, page=page, total_pages=total_pages)

    async def search(self, query: SearchQuery) -> SearchResult:
        items, total_pages = await self._search(
            keyword=query.keyword,
            categories=query.categories,
            page=query.page,
        )
        return SearchResult(items=items, page=query.page, total_pages=total_pages)

    async def get_torrent_detail(self, url: str) -> TorrentDetail:
        # url 可能是完整 detail_url（.../detail/123）或纯种子 ID，统一提取数字 ID
        m = re.search(r"(\d+)", url)
        if not m:
            raise TrackerParseError(
                "无法从 URL 中解析出 M-Team 种子 ID",
                details={"url": url},
            )
        torrent_id = m.group(1)

        data = await self._post_api("/api/torrent/detail", data={"id": torrent_id})
        if not data:
            raise TrackerParseError(
                "M-Team 种子详情为空，可能种子已删除或无权访问",
                details={"torrent_id": torrent_id},
            )

        status = data.get("status") or {}
        discount = status.get("discount", "NORMAL")
        download_factor = _DISCOUNT_MAP.get(discount, 1.0)
        upload_factor = 2.0 if discount in _UPLOAD_2X_DISCOUNTS else 1.0
        free_deadline = self._parse_datetime(status.get("discountEndTime"))
        if free_deadline is None and download_factor < 1.0:
            free_deadline = datetime.datetime.max

        size_bytes = int(data.get("size") or 0)
        site_cate_id = str(data.get("category")) if data.get("category") is not None else None

        imdb_id = None
        if data.get("imdb"):
            im = re.search(r"(tt\d+)", str(data.get("imdb")))
            imdb_id = im.group(1) if im else None

        # imageList 为图片 URL 数组（海报 + 截图），首图作为封面，整组存入 image_urls
        image_list = [str(u) for u in (data.get("imageList") or []) if u]

        return TorrentDetail(
            torrent_id=torrent_id,
            title=data.get("name") or "",
            subtitle=data.get("smallDescr") or "",
            category=self._resolve_category(site_cate_id),
            description=data.get("descr") or "",
            size=_bytes_to_human(size_bytes) if size_bytes else None,
            size_bytes=size_bytes,
            seeders=int(status.get("seeders", 0) or 0),
            leechers=int(status.get("leechers", 0) or 0),
            snatched=int(status.get("timesCompleted", 0) or 0),
            upload_time=self._parse_datetime(data.get("createdDate")),
            poster_url=image_list[0] if image_list else None,
            image_urls=image_list,
            free=download_factor == 0.0,
            free_deadline=free_deadline,
            download_volume_factor=download_factor,
            upload_volume_factor=upload_factor,
            imdb_id=imdb_id,
            download_url=torrent_id,
        )

    async def download_torrent(self, url: str) -> bytes:
        # url 为种子 ID（来自列表/搜索结果的 download_url）
        # 第一步：换取一次性下载令牌（直链）
        token_data = await self._post_api(
            "/api/torrent/genDlToken",
            data={"id": url},
        )
        if not token_data:
            raise TrackerParseError(
                "M-Team 未返回下载直链，可能未认证或达到下载频率限制",
                details={"torrent_id": url},
            )
        # 第二步：GET 直链下载 .torrent 文件字节
        return await self.client.download(token_data)

    async def get_user_profile(
        self,
        user_id: str | None = None,
    ) -> UserProfile:
        # M-Team API 基于 API-Key 识别身份，仅能查询当前登录用户，忽略 user_id 入参
        profile = await self._post_api("/api/member/profile")
        if not profile:
            raise TrackerParseError("获取 M-Team 用户资料失败，请检查 API-Key 是否有效")

        stat = profile.get("memberCount") or {}
        uploaded_bytes = int(stat.get("uploaded", 0) or 0)
        downloaded_bytes = int(stat.get("downloaded", 0) or 0)

        ratio: float | None
        try:
            ratio = float(stat.get("shareRate")) if stat.get("shareRate") is not None else None
        except (ValueError, TypeError):
            ratio = None

        bonus: float | None
        try:
            bonus = float(stat.get("bonus")) if stat.get("bonus") is not None else None
        except (ValueError, TypeError):
            bonus = None

        # 做种/下载计数来自独立的 peer 状态接口
        seeding = leeching = 0
        try:
            peer = await self._post_api("/api/tracker/myPeerStatus")
            if peer:
                seeding = int(peer.get("seeder", 0) or 0)
                leeching = int(peer.get("leecher", 0) or 0)
        except TrackerParseError:
            # peer 状态非关键信息，失败时不影响主资料返回
            logger.warning("获取 M-Team 做种状态失败，做种/下载计数将记为 0", exc_info=True)

        return UserProfile(
            user_id=str(profile.get("id", "")),
            username=profile.get("username") or "",
            user_class=str(profile.get("role") or ""),
            vip_group=False,
            uploaded=_bytes_to_human(uploaded_bytes),
            uploaded_bytes=uploaded_bytes,
            downloaded=_bytes_to_human(downloaded_bytes),
            downloaded_bytes=downloaded_bytes,
            ratio=ratio,
            bonus=bonus,
            seeding_count=seeding,
            leeching_count=leeching,
        )
