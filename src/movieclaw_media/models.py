"""影视数据层的对外数据模型。

字段形态刻意对齐前端发现页的渲染需求（apps/web/lib/media-types.ts）：
后端把「Hero 精选 + 分类横滚行」聚合成一屏完整数据，前端拿到即渲染，
不在浏览器端做二次编排。命名沿用项目 API 惯例的 snake_case，前端在
lib/api/discover.ts 做一次 camelCase 映射。
"""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel, Field


class MediaKind(str, Enum):
    """媒体类型：电影 / 剧集。取值与 TMDB 的路径段一致，可直接拼接 URL。"""

    MOVIE = "movie"
    TV = "tv"


class MediaSource(StrEnum):
    """媒体数据来源；ID 只在同一来源内部唯一。"""

    TMDB = "tmdb"
    DOUBAN = "douban"


class MediaCard(BaseModel):
    """一张海报卡片所需的全部字段（发现页列表项与 Hero 精选共用）。"""

    id: str = Field(description="TMDB 条目 ID（字符串形态，前端当作不透明键使用）")
    source: MediaSource = Field(default=MediaSource.TMDB, description="条目数据来源")
    type: MediaKind
    title: str = Field(description="中文标题（TMDB 无中文译名时为原名）")
    original_title: str = Field(description="原名（原语言）")
    year: int = Field(description="上映/首播年份")
    rating: float = Field(description="TMDB 评分（0~10，一位小数；0 表示暂无评分）")
    genres: list[str] = Field(default_factory=list, description="类型标签（中文，最多 3 个）")
    extent: str = Field(
        default="",
        description="规模：电影=片长、剧集=季数。TMDB 列表接口不含此字段，仅详情接口回填",
    )
    badges: list[str] = Field(
        default_factory=list,
        description="资源质量徽章（4K/HDR 等）。预留给后续站点资源匹配，当前恒为空",
    )
    overview: str = Field(default="", description="剧情简介（可能为空：小众条目无中文简介）")
    poster_url: str
    backdrop_url: str | None = Field(default=None, description="宽幅剧照，Hero 大横幅用")


class MediaRow(BaseModel):
    """发现页里一行横滚海报（如「热门电影」「高分经典」）。"""

    id: str
    title: str
    ranked: bool = Field(default=False, description="是否为 Top 10 大数字排名行")
    items: list[MediaCard]


class DiscoverPage(BaseModel):
    """一个完整发现页（发现电影 / 发现剧集各一份）。"""

    hero: list[MediaCard] = Field(description="Hero 大横幅轮播的精选项（均带 backdrop_url）")
    rows: list[MediaRow]


class MediaSearchItem(BaseModel):
    """轻量搜索候选条目（豆瓣/TMDB 共用）；不伪造来源未提供的字段。"""

    id: str
    source: MediaSource
    title: str
    year: int | None = Field(
        default=None, description="上映/首播年份；豆瓣轻量搜索不提供，恒为 None"
    )
    type: MediaKind | None = Field(
        default=None, description="movie/tv；豆瓣轻量搜索不提供，恒为 None"
    )
    rating: float = Field(default=0, description="来源站评分；0 表示暂无评分")
    poster_url: str


class MediaFacts(BaseModel):
    """详情页「词条信息」卡的字段（豆瓣式条目档案）。"""

    directors: list[str] = Field(default_factory=list, description="导演（剧集为主创）")
    cast: list[str] = Field(default_factory=list, description="主演（前 5 位）")
    country: str = Field(default="", description="制片地区")
    language: str = Field(default="", description="语言")
    released: str = Field(default="", description="上映/首播日期（ISO 格式）")
    network: str | None = Field(default=None, description="播出平台（仅剧集）")
    aliases: list[str] = Field(default_factory=list, description="别名/其他译名")
    source_url: str | None = Field(default=None, description="来源站条目地址")


class MediaImage(BaseModel):
    """一张剧照/海报：横滚条用预览图，灯箱看原图。"""

    preview_url: str = Field(description="缩略预览（剧照 w780 / 海报 w342）")
    full_url: str = Field(description="原图（original，灯箱全屏用）")
    width: int
    height: int


class MediaDetail(BaseModel):
    """条目详情：卡片字段（详情接口回填了 extent 等）+ 词条信息 + 图片 + 相似推荐。"""

    card: MediaCard
    facts: MediaFacts
    backdrops: list[MediaImage] = Field(default_factory=list, description="剧照（16:9 宽幅）")
    posters: list[MediaImage] = Field(default_factory=list, description="海报（2:3 竖版，配置语言优先）")
    related: list[MediaCard] = Field(default_factory=list, description="TMDB 推荐的相似作品")
