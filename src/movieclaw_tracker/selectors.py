from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as dc_replace


@dataclass(frozen=True)
class LoginSelectors:
    """登录流程选择器配置。

    与具体框架（如 NexusPHP）解耦，任何框架的登录流程都可以
    通过提供一组 LoginSelectors 来复用 CredentialAuthProvider。
    站点可在 YAML 中覆盖差异字段。
    """

    # 登录页路径（相对于 base_url）
    login_path: str = "login.php"
    # 用户名和密码的表单字段名
    username_field: str = "username"
    password_field: str = "password"
    # 登录成功判定：页面中出现 logout 链接
    success_css: str = "a[href*='logout']"
    # 登录失败时的错误信息 CSS 选择器
    error_css: str = "td.text"
    # 验证码图片 URL 的正则匹配模式（在登录页 HTML 中查找）
    captcha_url_re: str = r"(imagecode\.php\?[^\"']+)"
    # 验证码表单字段名
    captcha_field: str = "imagestring"
    # 验证码 hash 隐藏字段名（部分站点需要与验证码图片配对提交）
    captcha_hash_field: str = "imagehash"
    # 额外的固定登录表单参数（站点特殊字段）
    # 示例：ttg 需要 (("passan", ""), ("passid", "0"), ("lang", "0"))
    extra_form_data: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NexusPHPSelectors:
    """NexusPHP 框架专用的 CSS/XPath 选择器。

    每个字段都有标准 NexusPHP 的默认值。
    站点通过 YAML 配置覆盖差异字段。
    """

    # -- 登录 --
    login_path: str = "login.php"
    login_username_field: str = "username"
    login_password_field: str = "password"
    login_error_css: str = "td.text"
    login_success_css: str = "a[href*='logout']"

    # -- 种子列表 --
    torrent_list_path: str = "torrents.php"
    torrent_row_css: str = "table.torrents > tr:not(:first-child)"
    torrent_id_css: str = "a[href*='details.php']::attr(href)"
    torrent_title_css: str = "a[href*='details.php']"
    torrent_subtitle_css: str = "td.embedded > span"
    torrent_category_css: str = "td:nth-child(1) img::attr(class)"
    # 大小/做种/吸血/完成数：不带 ::text，由 _css_text 递归取嵌套文本
    # 这样无论数字是裸文本还是嵌在 <a>/<b> 中都能解析
    torrent_size_css: str = "td:nth-child(5)"
    torrent_seeders_css: str = "td:nth-child(6)"
    torrent_leechers_css: str = "td:nth-child(7)"
    torrent_snatched_css: str = "td:nth-child(8)"

    # 发布时间：优先从 span[title] 取精确时间戳（页面上显示的是 "X 小时前"，
    # 但 title 属性里存着 "2026-03-25 10:00:00" 格式的精确时间）
    torrent_time_css: str = "td:nth-child(4) > span::attr(title)"
    # 回退选择器：部分种子没有 span，日期直接写在 td 文本里
    torrent_time_fallback_css: str = "td:nth-child(4)"
    # strptime 格式
    torrent_time_fmt: str = "%Y-%m-%d %H:%M:%S"

    torrent_uploader_css: str = "td:nth-child(9) a"

    # -- 促销 --
    # CSS → 系数映射表。按声明顺序检查，第一个命中的生效；全部未命中时系数为 1.0。
    # 默认值覆盖标准 NexusPHP 的 img.pro_* 类名体系。
    # 站点通过 YAML 以字典形式覆盖（注册时自动转为 tuple），例：
    #   promo_download_rules:
    #     "span.torrent-pro-free": 0
    #     "span.torrent-pro-halfdown": 0.5
    promo_download_rules: tuple = (
        ("img.pro_free", 0.0),
        ("img.pro_free2up", 0.0),
        ("img.pro_50pctdown", 0.5),
        ("img.pro_50pctdown2up", 0.5),
        ("img.pro_30pctdown", 0.3),
    )
    promo_upload_rules: tuple = (
        ("img.pro_free2up", 2.0),
        ("img.pro_50pctdown2up", 2.0),
        ("img.pro_2up", 2.0),
    )

    # -- H&R（Hit-and-Run）标记 --
    # 命中该选择器的行标记为「有 H&R 考核」。空字符串表示站点不提供 / 未适配，
    # 此时字段保持 None（未知），不会误报成「无考核」。
    # 各站类名不同，需逐站确认后在 YAML 覆盖，例："img.hitandrun"。
    torrent_hr_css: str = ""

    # 促销截止时间：从该元素的指定属性中提取日期时间字符串。
    # 空字符串表示不解析截止时间（永久促销或站点不暴露该信息）。
    torrent_promo_deadline_css: str = ""
    torrent_promo_deadline_attr: str = "title"
    torrent_promo_deadline_re: str = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    torrent_promo_deadline_fmt: str = "%Y-%m-%d %H:%M:%S"
    torrent_detail_url_css: str = "a[href*='details.php']::attr(href)"
    torrent_download_url_css: str = "a[href*='download.php']::attr(href)"

    # -- 分页 --
    # 部分站点分页从 0 开始（如 SSD），page_offset = -1 表示发送 page-1 给服务端
    # 默认 0 表示不做偏移（多数站点 page=1 即第一页）
    page_offset: int = 0

    # -- 搜索 --
    search_path: str = "torrents.php"
    search_keyword_param: str = "search"

    # -- 详情页 --
    detail_title_css: str = "#top::text"
    detail_subtitle_css: str = "td.rowhead:contains('副标题') + td::text"
    detail_description_css: str = "#kdescr"
    detail_download_css: str = "a[href*='download.php']::attr(href)"
    detail_imdb_css: str = "a[href*='imdb.com']::attr(href)"
    detail_douban_css: str = "a[href*='douban.com']::attr(href)"
    detail_size_css: str = "td.rowhead:contains('大小') + td::text"
    detail_file_list_css: str = "td.rowfollow > ul > li::text"

    # -- 用户资料 --
    profile_path: str = "userdetails.php"
    # uid 选择器：留空表示不从页面提取，依赖调用方传入 user_id 参数。
    # 典型用法：取导航栏用户链接的 href 属性，再由代码用正则抽出数字。
    # 示例："a[href^='userdetails.php?id=']::attr(href)"
    profile_uid_css: str = ""
    profile_username_css: str = "h1::text"
    profile_class_css: str = "td.rowhead:contains('等级') + td::text"
    profile_uploaded_css: str = "td.rowhead:contains('上传量') + td::text"
    profile_downloaded_css: str = "td.rowhead:contains('下载量') + td::text"
    profile_ratio_css: str = "td.rowhead:contains('分享率') + td::text"
    profile_bonus_css: str = "td.rowhead:contains('魔力') + td::text"
    profile_join_date_css: str = "td.rowhead:contains('加入日期') + td::text"
    profile_seeding_css: str = "td.rowhead:contains('当前做种') + td::text"
    # 当前下载数（正在吸血的种子数）
    profile_leeching_css: str = "td.rowhead:contains('当前下载') + td::text"
    # VIP 标识元素；命中则视为 VIP 用户，默认为空（不检测）
    # 各站点可通过 YAML 配置覆盖为实际选择器，如 "img[src*='vip']"
    profile_vip_css: str = ""

    # -- 下载 --
    download_path: str = "download.php"

    def to_login_selectors(self) -> LoginSelectors:
        """从 NexusPHP 选择器中提取登录相关配置，生成通用 LoginSelectors。"""
        return LoginSelectors(
            login_path=self.login_path,
            username_field=self.login_username_field,
            password_field=self.login_password_field,
            success_css=self.login_success_css,
            error_css=self.login_error_css,
        )

    def replace(self, **kwargs: str) -> NexusPHPSelectors:
        """返回覆盖了指定字段的新实例。"""
        return dc_replace(self, **kwargs)
