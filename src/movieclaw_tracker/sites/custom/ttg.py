"""听听歌（TTG / ToTheGlory.im）自定义站点适配器。

TTG 基于 NexusPHP 框架，但在多处与标准实现差异较大，需要覆盖以下逻辑：

1. **分类 ID 为中文字符串**
   种子列表的分类由 ``<img alt="电影1080i/p">`` 等图片的 alt 属性表示，
   而标准 NexusPHP 使用纯数字 cat ID。框架默认的 ``_extract_category_id``
   只会提取数字，因此需要重写 ``_parse_torrent_row`` 直接使用原始 alt 值。

2. **搜索参数格式特殊**
   TTG 搜索使用单一 ``search_field`` 参数，分类过滤通过
   ``category:`分类名``` 前缀内嵌在关键词中，而非单独的 ``cat{id}`` 参数。
   IMDb ID 需把 ``tt`` 前缀替换为 ``imdb``。

3. **做种数/下载数共享同一 td**
   ``td:nth-child(9)`` 的文本格式为 ``"X/Y"``，需手动按 "/" 分割，
   无法用独立的 CSS 选择器分别提取。

4. **种子 ID 从 URL 路径提取**
   详情链接格式为 ``/t/12345``，而非标准的 ``?id=12345`` 查询参数。
   框架默认的 ``_extract_id_from_url`` 只处理查询参数，需直接正则路径。

5. **促销截止时间为中文日期**
   从 ``span[onclick]`` 的 onclick 属性提取，格式如 ``"2026年4月1日10点30分"``。
   此项通过 YAML 的 ``torrent_promo_deadline_*`` 选择器配置，父类直接处理。

6. **用户资料页结构特殊**
   数据位于 ``rules.php``，上传量/下载量/做种数/下载数通过
   ``<font>`` 元素的列表索引定位，分享率对 VIP 用户固定为无限大。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from parsel import Selector

from movieclaw_tracker.exceptions import TrackerParseError
from movieclaw_tracker.frameworks.nexusphp import NexusPHPSite
from movieclaw_tracker.models import (
    SearchQuery,
    SearchResult,
    TorrentCategory,
    TorrentListItem,
    TorrentListPage,
    UserProfile,
)

logger = logging.getLogger("movieclaw_tracker.sites.ttg")

# 种子 ID 正则：从路径格式 /t/12345 提取数字部分
_TORRENT_ID_RE = re.compile(r"/t/(\d+)")


class TTGSite(NexusPHPSite):
    """听听歌（ToTheGlory.im）站点适配器。

    继承 NexusPHPSite，仅覆盖与 TTG 页面结构不兼容的部分。
    促销系数、截止时间格式、发布时间格式等通过 ttg.yaml 的 selectors 配置，
    无需在此重写。
    """

    # ── 种子操作 ──────────────────────────────────────────────────────

    async def list_torrents(
        self,
        *,
        categories: list[TorrentCategory] | None = None,
        page: int = 1,
    ) -> TorrentListPage:
        """浏览种子列表。

        TTG 的浏览页为 browse.php，并固定携带 ``c=M`` 参数。
        TTG 不支持标准 NexusPHP 的 ``cat{id}=1`` 分类过滤方式，
        分类筛选只能通过搜索的 search_field 实现，此处列表不做分类过滤。
        """
        params: dict[str, Any] = {
            "page": page + self.selectors.page_offset,
            "c": "M",
        }
        url = self._url("browse.php")
        response = await self.client.get(url, params=params)
        doc = Selector(text=response.text)

        items = self._parse_torrent_rows(doc)
        total_pages = self._parse_total_pages(doc)

        return TorrentListPage(items=items, page=page, total_pages=total_pages)

    async def search(self, query: SearchQuery) -> SearchResult:
        """搜索种子。

        TTG 使用单一 ``search_field`` 参数，格式为：
        ``"category:`分类1` category:`分类2` 关键词"``

        若关键词为 IMDb ID（``tt\\d+``），自动将前缀 ``tt`` 替换为 ``imdb``，
        以匹配 TTG 内部的 IMDb 索引格式。
        """
        # 构建分类过滤前缀（每个站点分类 ID 一个 category: 标记）
        cate_parts: list[str] = []
        if query.categories and self.category_map:
            for cat in query.categories:
                for site_id in self.category_map.get(cat, []):
                    cate_parts.append(f"category:`{site_id}`")

        # 处理关键词：IMDb ID 需替换 tt 前缀为 imdb
        keyword = query.keyword or ""
        if re.match(r"^tt\d+$", keyword, re.IGNORECASE):
            keyword = re.sub(r"^tt", "imdb", keyword, count=1, flags=re.IGNORECASE)

        search_field = " ".join(cate_parts + [keyword]).strip()

        params: dict[str, Any] = {
            "search_field": search_field,
            "page": query.page + self.selectors.page_offset,
            "c": "M",
        }
        url = self._url("browse.php")
        response = await self.client.get(url, params=params)
        doc = Selector(text=response.text)

        items = self._parse_torrent_rows(doc)
        total_pages = self._parse_total_pages(doc)

        return SearchResult(items=items, page=query.page, total_pages=total_pages)

    # ── 用户操作 ──────────────────────────────────────────────────────

    async def get_user_profile(
        self,
        user_id: str | None = None,
    ) -> UserProfile:
        """获取用户资料。

        TTG 的用户统计数据位于 ``rules.php`` 页面，通过 ``<font>`` 元素的
        列表索引定位，结构与标准 NexusPHP 的 userdetails.php 差异较大。

        各 font 元素的数据语义（按原始页面中的出现顺序）：
        - ``fonts[3]``：分享率（非 VIP；VIP 用户分享率固定为无限大）
        - ``fonts[-7]``：上传量
        - ``fonts[-5]``：下载量
        - ``fonts[-3]``：当前做种数
        - ``fonts[-2]``：当前下载数
        """
        url = self._url("rules.php")
        response = await self.client.get(url)
        doc = Selector(text=response.text)

        # 用户链接同时携带 uid（href 属性）、用户名（文本）和等级（class 属性）
        user_link = doc.css('a[href^="/userdetails.php?id="]')
        if not user_link:
            raise TrackerParseError(
                "无法在 rules.php 中定位 TTG 用户链接，请检查是否已登录或页面结构是否变更",
                details={"url": url},
            )

        uid_href = user_link.attrib.get("href", "")
        uid_match = re.search(r"\d+", uid_href)
        uid = uid_match.group(0) if uid_match else (user_id or "")

        username = user_link.css("::text").get("").strip()
        user_class = user_link.attrib.get("class", "").strip()

        # img[alt="donor"] 存在时视为 VIP/捐赠者
        vip_group = bool(doc.css('img[alt="donor"]'))

        # 按索引提取 font 元素文本，越界时返回空字符串
        fonts = doc.css("font")

        def _font_text(index: int) -> str:
            try:
                return fonts[index].css("::text").get("").strip()
            except IndexError:
                return ""

        uploaded_text = _font_text(-7)
        downloaded_text = _font_text(-5)
        seeding_text = _font_text(-3)
        leeching_text = _font_text(-2)

        # VIP 分享率视为无限大；普通用户从 font[3] 读取，"无限" 同样映射为 inf
        ratio: float | None
        if vip_group:
            ratio = float("inf")
        else:
            ratio_raw = _font_text(3).replace("无限", "inf")
            ratio = float("inf") if ratio_raw == "inf" else self._parse_float(ratio_raw)

        return UserProfile(
            user_id=uid,
            username=username,
            user_class=user_class,
            vip_group=vip_group,
            uploaded=uploaded_text,
            uploaded_bytes=self._parse_size_bytes(uploaded_text) or 0,
            downloaded=downloaded_text,
            downloaded_bytes=self._parse_size_bytes(downloaded_text) or 0,
            ratio=ratio,
            seeding_count=self._parse_int(seeding_text) or 0,
            leeching_count=self._parse_int(leeching_text) or 0,
        )

    # ── 内部解析方法 ──────────────────────────────────────────────────

    def _parse_torrent_row(self, row: Selector) -> TorrentListItem | None:
        """解析 TTG 种子列表中的单行数据。

        TTG 种子行 HTML 结构要点：

        - **详情链接**：``div.name_left > a``，href 格式为 ``/t/12345``，
          种子 ID 从路径中正则提取，而非 ``?id=`` 查询参数。

        - **标题**：``div.name_left > a > b``，内嵌 ``<span>`` 存放描述信息（如"免费"标记），
          取 ``<b>`` 的直接文本节点（XPath ``text()``）以排除 span 内容；
          若直接文本为空则回退为全文本。

        - **分类**：``td:nth-child(1) > a > img`` 的 ``alt`` 属性，值为中文字符串
          （如 "电影1080i/p"），直接作为分类 ID 查找反向映射表。

        - **大小**：``td:nth-child(7)``。

        - **做种数/下载数**：共享 ``td:nth-child(9)``，文本格式为 ``"X/Y"``，
          按 ``/`` 分割后分别解析。

        - **完成次数**：``td:nth-child(8)``，文本含 ``"次"`` 后缀，需去除。

        - **发布时间**：由父类 ``_parse_upload_time`` 处理，格式通过 YAML 配置。

        - **促销系数/截止时间**：由父类 ``_parse_promo`` 处理，规则通过 YAML 配置。

        - **下载按钮**：``a.dl_a``，而非标准的 ``download.php`` 链接。
        """
        detail_href = row.css("div.name_left > a::attr(href)").get("")
        if not detail_href:
            return None

        # 从路径格式 /t/12345 提取种子 ID
        id_match = _TORRENT_ID_RE.search(detail_href)
        if not id_match:
            # 路径格式不符时退回通用数字正则，避免整行被丢弃
            fallback = re.search(r"\d+", detail_href)
            torrent_id = fallback.group(0) if fallback else ""
        else:
            torrent_id = id_match.group(1)

        # 标题：优先取 <b> 的直接文本节点，排除内嵌 <span>（免费标记等）的内容
        title_parts = row.css("div.name_left > a > b").xpath("text()").getall()
        title = " ".join(t.strip() for t in title_parts if t.strip())
        if not title:
            title = self._css_text(row, "div.name_left > a > b") or ""

        # 分类：img[alt] 属性值即为 TTG 的中文分类 ID，直接查反向映射
        cat_raw = row.css("td:nth-child(1) > a > img::attr(alt)").get("").strip()
        category = self._reverse_category_map.get(cat_raw, TorrentCategory.OTHER)

        # 促销系数与截止时间（父类通过 YAML 中配置的 promo_*_rules 处理）
        dl_factor, ul_factor, free_deadline = self._parse_promo(row)

        # 发布时间（父类通过 YAML 中配置的 torrent_time_* 处理）
        upload_time = self._parse_upload_time(row)

        # 下载链接
        download_href = row.css("a.dl_a::attr(href)").get("")

        # 大小
        size_text = self._css_text(row, "td:nth-child(7)")

        # 做种数/下载数：共享 td:nth-child(9)，格式 "X/Y"
        sl_text = self._css_text(row, "td:nth-child(9)") or ""
        sl_parts = sl_text.split("/")
        seeders = self._parse_int(sl_parts[0].strip()) or 0
        leechers = (
            self._parse_int(sl_parts[1].replace("\n", "").strip()) or 0
            if len(sl_parts) > 1
            else 0
        )

        # 完成次数：去除 "次" 字后缀
        grabs_text = (self._css_text(row, "td:nth-child(8)") or "").replace("次", "")
        snatched = self._parse_int(grabs_text.strip()) or 0

        return TorrentListItem(
            torrent_id=torrent_id,
            title=title.strip(),
            category=category if cat_raw else None,
            site_category_id=cat_raw or None,
            site_category_name=cat_raw or None,
            size=size_text,
            size_bytes=self._parse_size_bytes(size_text) or 0,
            seeders=seeders,
            leechers=leechers,
            snatched=snatched,
            upload_time=upload_time,
            free=dl_factor < 1.0,
            free_deadline=free_deadline,
            download_volume_factor=dl_factor,
            upload_volume_factor=ul_factor,
            # detail_url 给用户在浏览器打开，用网页域名；download_url 由程序请求，用 base_url
            detail_url=self._ensure_absolute_web(detail_href) if detail_href else None,
            download_url=self._ensure_absolute(download_href) if download_href else None,
        )
