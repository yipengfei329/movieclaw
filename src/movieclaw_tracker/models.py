from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class TorrentCategory(str, Enum):
    """应用级一级分类枚举。"""

    MOVIE = "movie"
    TV = "tv"
    DOCUMENTARY = "documentary"
    ANIME = "anime"
    MUSIC = "music"
    GAME = "game"
    AV = "av"
    OTHER = "other"


class AuthState(str, Enum):
    """认证状态。"""

    AUTHENTICATED = "authenticated"
    UNAUTHENTICATED = "unauthenticated"
    EXPIRED = "expired"
    NEEDS_CAPTCHA = "needs_captcha"
    FAILED = "failed"


class AuthResult(BaseModel):
    """认证操作返回结果。"""

    success: bool
    state: AuthState
    cookies: dict[str, str] | None = None
    message: str | None = None
    captcha_image_url: str | None = None


class TorrentListItem(BaseModel):
    """种子列表中的单条记录。

    默认值规则：
    - 数值计数（seeders 等）默认 0 —— 解析不到即视为无数据，便于排序和过滤。
    - 促销系数默认 1.0（正常）、free 默认 False —— 解析不到即视为无促销。
    - 展示文本（subtitle / uploader）默认空字符串 —— 方便前端直接拼接。
    - 时间、URL、分类等真正可选的字段保持 None —— 信息确实可能不存在。
    """

    torrent_id: str
    title: str
    subtitle: str = ""
    category: TorrentCategory | None = None
    site_category_id: str | None = None
    site_category_name: str | None = None
    size: str | None = None
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0
    snatched: int = 0
    upload_time: datetime | None = None
    uploader: str = ""
    poster_url: str | None = None
    # 促销信息
    free: bool = False
    free_deadline: datetime | None = None
    download_volume_factor: float = 1.0   # 下载系数，0=全免，0.5=半免，1=正常
    upload_volume_factor: float = 1.0     # 上传系数，2=双倍，1=正常
    detail_url: str | None = None
    download_url: str | None = None


class TorrentListPage(BaseModel):
    """分页的种子列表。"""

    items: list[TorrentListItem]
    page: int
    total_pages: int | None = None


class TorrentDetail(BaseModel):
    """种子详情页完整信息。默认值规则同 TorrentListItem。"""

    torrent_id: str
    title: str
    subtitle: str = ""
    category: TorrentCategory | None = None
    description: str = ""
    size: str | None = None
    size_bytes: int = 0
    seeders: int = 0
    leechers: int = 0
    snatched: int = 0
    upload_time: datetime | None = None
    uploader: str = ""
    poster_url: str | None = None
    # 促销信息
    free: bool = False
    free_deadline: datetime | None = None
    download_volume_factor: float = 1.0
    upload_volume_factor: float = 1.0
    # 做种要求
    minimum_ratio: float | None = None
    minimum_seed_time: int | None = None          # 单位：秒
    # 外部关联
    imdb_id: str | None = None
    douban_id: str | None = None
    file_list: list[str] = Field(default_factory=list)
    download_url: str | None = None


class SearchQuery(BaseModel):
    """搜索请求参数。"""

    keyword: str
    categories: list[TorrentCategory] | None = None
    page: int = 1


class SearchResult(BaseModel):
    """搜索结果。"""

    items: list[TorrentListItem]
    page: int
    total_pages: int | None = None
    total_results: int | None = None


class UserProfile(BaseModel):
    """PT 站点用户资料。

    字段说明：
    - user_id: 站点用户 ID（字符串，兼容数字和非数字 ID）
    - username: 用户名
    - user_class: 用户等级（如：Power User、Elite 等）
    - vip_group: 是否属于 VIP 用户组；部分站点无此概念时保持 False
    - join_date: 注册日期
    - uploaded / uploaded_bytes: 上传量（可读文本 / 字节数）
    - downloaded / downloaded_bytes: 下载量（可读文本 / 字节数）
    - ratio: 分享率
    - bonus: 魔力值（积分）
    - seeding_count: 当前做种数
    - leeching_count: 当前下载数（正在吸血的种子数）
    - avatar_url: 头像地址
    """

    user_id: str
    username: str
    user_class: str = ""
    vip_group: bool = False
    join_date: datetime | None = None
    uploaded: str = ""
    uploaded_bytes: int = 0
    downloaded: str = ""
    downloaded_bytes: int = 0
    ratio: float | None = None       # None = 未知；0.0 有实际含义（无上传），不可混淆
    bonus: float | None = None       # None = 站点不提供或未解析到
    seeding_count: int = 0
    leeching_count: int = 0
    avatar_url: str | None = None
