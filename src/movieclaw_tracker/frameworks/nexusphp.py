from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

from parsel import Selector

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
from movieclaw_tracker.selectors import NexusPHPSelectors

logger = logging.getLogger("movieclaw_tracker.frameworks.nexusphp")


class NexusPHPSite(BaseSite):
    """NexusPHP 框架的完整 HTML 解析实现。"""

    def __init__(
        self,
        *,
        selectors: NexusPHPSelectors,
        category_map: dict[TorrentCategory, list[str]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.selectors = selectors
        self.category_map = category_map or {}
        self._reverse_category_map: dict[str, TorrentCategory] = {
            site_id: cat
            for cat, ids in self.category_map.items()
            for site_id in ids
        }

    # -- 种子操作 ----------------------------------------------------------

    async def list_torrents(
        self,
        *,
        categories: list[TorrentCategory] | None = None,
        page: int = 1,
    ) -> TorrentListPage:
        # page_offset 处理 0-indexed 站点：page=1 → 发送 0，page=2 → 发送 1
        params: dict[str, Any] = {"page": page + self.selectors.page_offset}
        self._apply_category_params(params, categories)

        url = self._url(self.selectors.torrent_list_path)
        response = await self.client.get(url, params=params)
        doc = Selector(text=response.text)

        items = self._parse_torrent_rows(doc)
        total_pages = self._parse_total_pages(doc)

        return TorrentListPage(items=items, page=page, total_pages=total_pages)

    async def search(self, query: SearchQuery) -> SearchResult:
        params: dict[str, Any] = {
            self.selectors.search_keyword_param: query.keyword,
            "page": query.page + self.selectors.page_offset,
        }
        self._apply_category_params(params, query.categories)

        url = self._url(self.selectors.search_path)
        response = await self.client.get(url, params=params)
        doc = Selector(text=response.text)

        items = self._parse_torrent_rows(doc)
        total_pages = self._parse_total_pages(doc)

        return SearchResult(
            items=items,
            page=query.page,
            total_pages=total_pages,
        )

    async def get_torrent_detail(self, url: str) -> TorrentDetail:
        full_url = self._ensure_absolute(url)
        response = await self.client.get(full_url)
        doc = Selector(text=response.text)

        torrent_id = self._extract_id_from_url(full_url, "id")

        title = self._css_text(doc, self.selectors.detail_title_css)
        if not title:
            raise TrackerParseError(
                "Failed to parse torrent detail title",
                details={"url": full_url},
            )

        download_href = doc.css(self.selectors.detail_download_css).get("")
        download_url = self._ensure_absolute(download_href) if download_href else None

        imdb_href = doc.css(self.selectors.detail_imdb_css).get("")
        imdb_id = self._extract_imdb_id(imdb_href) if imdb_href else None

        douban_href = doc.css(self.selectors.detail_douban_css).get("")
        douban_id = self._extract_douban_id(douban_href) if douban_href else None

        file_list = doc.css(self.selectors.detail_file_list_css).getall()

        return TorrentDetail(
            torrent_id=torrent_id,
            title=title,
            subtitle=self._field_str(doc, self.selectors.detail_subtitle_css),
            description=self._field_str(doc, self.selectors.detail_description_css),
            size=self._field_optional_str(doc, self.selectors.detail_size_css),
            download_url=download_url,
            imdb_id=imdb_id,
            douban_id=douban_id,
            file_list=[f.strip() for f in file_list if f.strip()],
        )

    async def download_torrent(self, url: str) -> bytes:
        full_url = self._ensure_absolute(url)
        return await self.client.download(full_url)

    # -- 用户操作 ----------------------------------------------------------

    async def get_user_profile(
        self,
        user_id: str | None = None,
    ) -> UserProfile:
        params: dict[str, str] = {}
        if user_id:
            params["id"] = user_id

        url = self._url(self.selectors.profile_path)
        response = await self.client.get(url, params=params)
        doc = Selector(text=response.text)

        username = self._css_text(doc, self.selectors.profile_username_css)
        if not username:
            raise TrackerParseError(
                "Failed to parse user profile",
                details={"url": url},
            )

        # 若调用方未提供 user_id，尝试从页面提取（通常取导航栏用户链接的 href 中的数字）
        if not user_id and self.selectors.profile_uid_css:
            uid_href = self._css_text(doc, self.selectors.profile_uid_css)
            if uid_href:
                uid_match = re.search(r"\d+", uid_href)
                user_id = uid_match.group() if uid_match else ""

        ratio_text = self._css_text(doc, self.selectors.profile_ratio_css)
        ratio = self._parse_float(ratio_text) if ratio_text else None

        bonus_text = self._css_text(doc, self.selectors.profile_bonus_css)
        bonus = self._parse_float(bonus_text) if bonus_text else None

        seeding_text = self._css_text(doc, self.selectors.profile_seeding_css)
        seeding_count = self._parse_int(seeding_text) if seeding_text else None

        leeching_text = self._css_text(doc, self.selectors.profile_leeching_css)
        leeching_count = self._parse_int(leeching_text) if leeching_text else None

        # 若站点配置了 VIP 选择器且能命中元素，则标记为 VIP 用户
        vip_group = (
            bool(doc.css(self.selectors.profile_vip_css))
            if self.selectors.profile_vip_css
            else False
        )

        return UserProfile(
            user_id=user_id or "",
            username=username,
            user_class=self._field_str(doc, self.selectors.profile_class_css),
            vip_group=vip_group,
            uploaded=self._field_str(doc, self.selectors.profile_uploaded_css),
            downloaded=self._field_str(doc, self.selectors.profile_downloaded_css),
            ratio=ratio,
            bonus=bonus,
            seeding_count=self._parse_int(seeding_text) or 0,
            leeching_count=self._parse_int(leeching_text) or 0,
            join_date=None,
        )

    # -- 内部解析方法 ------------------------------------------------------

    def _parse_promo(
        self, row: Selector
    ) -> tuple[float, float, "datetime | None"]:
        """解析单行的促销信息，返回 (下载系数, 上传系数, 截止时间)。

        按 promo_download_rules / promo_upload_rules 中声明的顺序逐条匹配，
        第一条命中的规则生效；全部未命中时系数为 1.0（正常）。

        各站点通过 YAML 覆盖规则表即可适配不同的促销 CSS 类名和系数。
        """
        sel = self.selectors

        # -- 下载系数 --
        download_factor = 1.0
        for css, factor in sel.promo_download_rules:
            if row.css(css):
                download_factor = factor
                break

        # -- 上传系数 --
        upload_factor = 1.0
        for css, factor in sel.promo_upload_rules:
            if row.css(css):
                upload_factor = factor
                break

        # -- 截止时间 --
        # 仅在有促销且配置了截止时间选择器时尝试解析
        deadline: datetime | None = None
        if sel.torrent_promo_deadline_css and download_factor < 1.0:
            nodes = row.css(sel.torrent_promo_deadline_css)
            if nodes:
                raw = nodes.attrib.get(sel.torrent_promo_deadline_attr, "")
                match = re.search(sel.torrent_promo_deadline_re, raw)
                if match:
                    try:
                        deadline = datetime.strptime(
                            match.group(0), sel.torrent_promo_deadline_fmt
                        )
                    except ValueError:
                        logger.debug(
                            "促销截止时间解析失败: raw=%r fmt=%s",
                            raw, sel.torrent_promo_deadline_fmt,
                        )

        return download_factor, upload_factor, deadline

    def _parse_upload_time(self, row: Selector) -> "datetime | None":
        """解析种子的发布时间。

        优先从 span[title] 的 title 属性取精确时间戳（页面显示 "X小时前"
        但 title 里存的是 "2026-03-25 10:00:00" 格式）；
        若无 span（少数老种子），回退取 td 文本内容。
        """
        sel = self.selectors

        # 优先：span 的 title 属性（已含 ::attr(title) 伪元素）
        raw = self._css_text(row, sel.torrent_time_css)

        # 回退：td 纯文本
        if not raw and sel.torrent_time_fallback_css:
            raw = self._css_text(row, sel.torrent_time_fallback_css)

        if not raw:
            return None

        raw = raw.strip()
        try:
            return datetime.strptime(raw, sel.torrent_time_fmt)
        except ValueError:
            logger.debug("发布时间解析失败: raw=%r fmt=%s", raw, sel.torrent_time_fmt)
            return None

    def _parse_torrent_rows(self, doc: Selector) -> list[TorrentListItem]:
        rows = doc.css(self.selectors.torrent_row_css)
        items: list[TorrentListItem] = []
        for row in rows:
            item = self._parse_torrent_row(row)
            if item:
                items.append(item)
        return items

    def _parse_torrent_row(self, row: Selector) -> TorrentListItem | None:
        detail_href = row.css(self.selectors.torrent_detail_url_css).get("")
        if not detail_href:
            return None

        torrent_id = self._extract_id_from_url(detail_href, "id")
        # 优先取 <a title="完整英文名"> 属性（部分站点链接文字会被截断）
        # 若无 title 属性则退回链接文字
        title_node = row.css(self.selectors.torrent_title_css)
        title = title_node.attrib.get("title", "").strip()
        if not title:
            title = self._css_text(row, f"{self.selectors.torrent_title_css}::text") or ""

        # 分类映射
        cat_raw = row.css(self.selectors.torrent_category_css).get("")
        site_cat_id = self._extract_category_id(cat_raw)
        category = self._reverse_category_map.get(site_cat_id, TorrentCategory.OTHER)

        # 促销解析
        dl_factor, ul_factor, free_deadline = self._parse_promo(row)

        # 发布时间
        upload_time = self._parse_upload_time(row)

        download_href = row.css(self.selectors.torrent_download_url_css).get("")

        sel = self.selectors
        size_text = self._css_text(row, sel.torrent_size_css)

        return TorrentListItem(
            torrent_id=torrent_id,
            title=title.strip(),
            subtitle=self._field_str(row, sel.torrent_subtitle_css),
            category=category if site_cat_id else None,
            site_category_id=site_cat_id or None,
            size=size_text,
            size_bytes=self._field_size_bytes(row, sel.torrent_size_css),
            seeders=self._field_int(row, sel.torrent_seeders_css),
            leechers=self._field_int(row, sel.torrent_leechers_css),
            snatched=self._field_int(row, sel.torrent_snatched_css),
            uploader=self._field_str(row, sel.torrent_uploader_css),
            upload_time=upload_time,
            free=dl_factor < 1.0,
            free_deadline=free_deadline,
            download_volume_factor=dl_factor,
            upload_volume_factor=ul_factor,
            detail_url=self._ensure_absolute(detail_href) if detail_href else None,
            download_url=self._ensure_absolute(download_href) if download_href else None,
        )

    def _parse_total_pages(self, doc: Selector) -> int | None:
        """尝试从分页导航中提取总页数。"""
        last_page_links = doc.css("a[href*='page=']")
        max_page = 1
        for link in last_page_links:
            href = link.attrib.get("href", "")
            match = re.search(r"page=(\d+)", href)
            if match:
                page_num = int(match.group(1))
                if page_num > max_page:
                    max_page = page_num
        return max_page if max_page > 1 else None

    # -- 分类参数构建 ------------------------------------------------------

    def _apply_category_params(
        self,
        params: dict[str, Any],
        categories: list[TorrentCategory] | None,
    ) -> None:
        """将应用级分类转为 NexusPHP 的 cat 参数。"""
        if not categories or not self.category_map:
            return
        for cat in categories:
            site_ids = self.category_map.get(cat, [])
            for sid in site_ids:
                params[f"cat{sid}"] = 1

    # -- 字段级提取方法 ----------------------------------------------------
    # 每个方法封装"取文本 + 类型转换 + 该类型的语义默认值"，
    # 调用方只需选择正确的方法名，不需要在调用处再写 or 0 / or "" 之类的兜底。

    def _field_int(self, sel: Selector, css: str) -> int:
        """提取整数字段。解析失败返回 0（计数类字段的语义零值）。"""
        return self._parse_int(self._css_text(sel, css)) or 0

    def _field_str(self, sel: Selector, css: str) -> str:
        """提取必填文本字段。解析失败返回空字符串（展示类字段的语义空值）。"""
        return self._css_text(sel, css) or ""

    def _field_optional_str(self, sel: Selector, css: str) -> str | None:
        """提取可选文本字段。解析失败返回 None（字段本身可能不存在）。"""
        return self._css_text(sel, css)

    def _field_size_bytes(self, sel: Selector, css: str) -> int:
        """提取文件大小字段，转为字节数。解析失败返回 0。"""
        return self._parse_size_bytes(self._css_text(sel, css)) or 0

    # -- 工具方法 ----------------------------------------------------------

    def _ensure_absolute(self, url: str) -> str:
        if url.startswith(("http://", "https://")):
            return url
        return urljoin(self.base_url + "/", url)

    @staticmethod
    def _css_text(sel: Selector, css: str) -> str | None:
        """从选择器提取纯文本。

        - 空选择器直接返回 None（站点配置为空表示该字段不存在）；
        - ``::next_sibling_text``：部分 NexusPHP 站点（如 SSD）的 HTML 结构中，
          font.color_* 等标记元素本身为空，实际值存放在其后紧跟的文本节点兄弟里：
          ``<font class="color_ratio"></font>2.31``。
          此伪元素通过 XPath ``following-sibling::text()[1]`` 取该文本节点；
        - 其他含 ``::`` 的选择器（``::text``、``::attr(...)``）视为标准伪元素，直接求值；
        - 普通元素选择器时递归收集所有子文本节点并拼接，
          避免 ``.get()`` 返回原始 HTML 字符串。
        """
        if not css:
            return None
        # ::next_sibling_text：取目标元素之后第一个文本节点兄弟
        if css.endswith("::next_sibling_text"):
            element_css = css[: -len("::next_sibling_text")]
            elements = sel.css(element_css)
            if not elements:
                return None
            text = elements.xpath("following-sibling::text()[1]").get()
            return text.strip() if text else None
        if "::" in css:
            # 标准伪元素选择器：直接取值
            text = sel.css(css).get()
            return text.strip() if text else None
        # 普通元素选择器：递归收集所有文本节点（含嵌套子元素）
        texts = sel.css(css).css("::text").getall()
        if texts:
            result = " ".join(t.strip() for t in texts if t.strip())
            return result or None
        return None

    @staticmethod
    def _extract_id_from_url(url: str, param: str = "id") -> str:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        ids = qs.get(param, [])
        return ids[0] if ids else ""

    @staticmethod
    def _extract_category_id(raw: str) -> str:
        match = re.search(r"(\d+)", raw)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_imdb_id(url: str) -> str | None:
        match = re.search(r"(tt\d+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _extract_douban_id(url: str) -> str | None:
        match = re.search(r"subject/(\d+)", url)
        return match.group(1) if match else None

    @staticmethod
    def _parse_int(text: str | None) -> int | None:
        if not text:
            return None
        cleaned = re.sub(r"[,\s]", "", text.strip())
        try:
            return int(cleaned)
        except ValueError:
            return None

    # 大小单位 → 字节乘数
    _SIZE_UNITS: dict[str, float] = {
        "b": 1,
        "kb": 1024,
        "mb": 1024**2,
        "gb": 1024**3,
        "tb": 1024**4,
        "pb": 1024**5,
        # 部分站点使用 KiB/MiB 标记
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }

    @classmethod
    def _parse_size_bytes(cls, text: str | None) -> int | None:
        """将人可读的大小文本转为字节数。

        支持格式：``"25.6 GB"``、``"1,024.5 MB"``、``"500KB"`` 等。
        """
        if not text:
            return None
        match = re.match(r"([\d,]+(?:\.\d+)?)\s*([a-zA-Z]+)", text.strip())
        if not match:
            return None
        num_str = match.group(1).replace(",", "")
        unit = match.group(2).lower()
        multiplier = cls._SIZE_UNITS.get(unit)
        if multiplier is None:
            return None
        try:
            return int(float(num_str) * multiplier)
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _parse_float(text: str | None) -> float | None:
        if not text:
            return None
        cleaned = re.sub(r"[,\s]", "", text.strip())
        try:
            return float(cleaned)
        except ValueError:
            return None
