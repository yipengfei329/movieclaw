"""影视数据层的对外数据模型。

字段形态刻意对齐前端发现页的渲染需求（apps/web/lib/media-types.ts）：
后端先给出「布局」（行清单），前端据此撑起骨架，再按行拉取数据渐进填充；
每行内部仍是拿到即渲染，不在浏览器端做二次编排。命名沿用项目 API 惯例的
snake_case，前端在 lib/api/discover.ts 做一次 camelCase 映射。
"""

from __future__ import annotations

from enum import Enum, StrEnum

from pydantic import BaseModel, Field


class MediaKind(str, Enum):  # noqa: UP042 —— 改 StrEnum 会改变 str()/f-string 输出，牵连面大，维持现状
    """媒体类型：电影 / 剧集。取值与 TMDB 的路径段一致，可直接拼接 URL。"""

    MOVIE = "movie"
    TV = "tv"


class MediaSource(StrEnum):
    """媒体数据来源；ID 只在同一来源内部唯一。"""

    TMDB = "tmdb"
    DOUBAN = "douban"


class MediaLibraryStatus(BaseModel):
    """发现卡片对应的轻量库存摘要。

    这是发现页与本地库存之间唯一共享的列表级契约：只表达是否存在在位
    文件及其聚合数量，不携带文件路径、介质规格或探测 JSON，避免海报墙
    查询扩大为逐条目明细读取。
    """

    media_item_id: int = Field(description="本地媒体条目 id，用于详情深链")
    library_count: int = Field(description="包含在位文件的媒体库数量")
    file_count: int = Field(description="在位文件数量")


class MediaLibraryLink(BaseModel):
    """发现详情跳转到一个本地媒体库条目所需的最小身份信息。"""

    library_id: int = Field(description="媒体库 id")
    library_name: str = Field(description="媒体库展示名称")
    media_item_id: int = Field(description="本地媒体条目 id")


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
    library_status: MediaLibraryStatus | None = Field(
        default=None,
        description="本地在位库存摘要；未精确命中或只有缺失台账时为 null",
    )


class MediaRow(BaseModel):
    """发现页里一行横滚海报（如「热门电影」「高分经典」）。"""

    id: str
    title: str
    ranked: bool = Field(default=False, description="是否为 Top 10 大数字排名行")
    items: list[MediaCard]


class DiscoverRowStub(BaseModel):
    """布局里的一行占位（只有标识与标题，不含条目数据）。

    前端拿到布局后先按行清单撑起整页骨架，再逐行请求数据填充——
    这是发现页渐进加载的关键：不必等最慢的榜单，先到先渲染。
    """

    id: str
    title: str
    ranked: bool = Field(default=False, description="是否为 Top 10 大数字排名行")


class DiscoverLayout(BaseModel):
    """发现页布局（发现电影 / 发现剧集各一份）：纯配置，毫秒级返回。"""

    has_hero: bool = Field(description="是否有 Hero 大横幅（豆瓣视角没有）")
    rows: list[DiscoverRowStub]


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


class MediaCastMember(BaseModel):
    """演职员表的一行：姓名 + 饰演角色 + 头像。

    发现页详情要按「演职员横滚条」呈现（与媒体库条目详情同一套版式），
    只有姓名撑不起那个版式，因此比原先的 ``list[str]`` 多带角色与头像。
    头像在数据源里常常缺失（小众条目、配音演员），前端按占位渲染，
    不必为此过滤掉这个人——名字与角色本身就是有效信息。
    """

    name: str = Field(description="演员姓名")
    role: str | None = Field(default=None, description="饰演角色；数据源未提供为空")
    avatar_url: str | None = Field(default=None, description="头像地址；数据源未提供为空")
    tmdb_person_id: int | None = Field(
        default=None,
        description="TMDB 影人 ID；有值时前端把这一格链到人物页。豆瓣来源没有此 id",
    )


class MediaFacts(BaseModel):
    """详情页「词条信息」卡的字段（豆瓣式条目档案）。"""

    directors: list[str] = Field(default_factory=list, description="导演（剧集为主创）")
    cast: list[MediaCastMember] = Field(
        default_factory=list,
        description="演职员（按数据源给出的主次顺序，最多 _CAST_LIMIT 位）",
    )
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
    posters: list[MediaImage] = Field(
        default_factory=list, description="海报（2:3 竖版，配置语言优先）"
    )
    related: list[MediaCard] = Field(default_factory=list, description="TMDB 推荐的相似作品")
    library_links: list[MediaLibraryLink] = Field(
        default_factory=list,
        description="主条目可跳转的本地媒体库入口；相似推荐不带此字段",
    )
