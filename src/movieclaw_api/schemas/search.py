"""跨站点聚合搜索的返回 Schema。

设计要点
--------
- ``TorrentHit`` 直接**继承** tracker 的 ``TorrentListItem``：后者已含标题 / 大小 /
  做种数 / 促销系数等全部展示字段，这里只需再挂两个「来源标识」，前端就能在合并后的
  大列表里给每一条结果打上「它来自哪个站点」的标签。继承而非重写，避免字段两头维护。
- ``SiteSearchStatus`` 逐站汇报执行情况：命中条数与失败原因。前端据此提示「A 站搜到 12
  条、B 站认证过期」，而不是把一次单站故障当成「全网都没结果」。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_serializer, field_validator

from movieclaw_db.models.search_history import SearchHistory
from movieclaw_enrich import TorrentAttrs
from movieclaw_tracker.models import TorrentCategory, TorrentListItem


class TorrentHit(TorrentListItem):
    """搜索结果里的单条种子——在 ``TorrentListItem`` 基础上补上来源站点与扩充属性。"""

    site_id: str
    site_name: str
    # 数据扩充层从标题/副标题推导的结构化属性（年份/分辨率/编码/压制组/季集...）。
    # 搜索链路现算现返，不落库——规则升级后下次搜索立即生效。
    attrs: TorrentAttrs | None = None


class SiteSearchStatus(BaseModel):
    """单个站点在本次搜索中的执行情况。"""

    site_id: str
    site_name: str
    count: int  # 该站命中条数
    error: str | None = None  # 失败原因（可读中文）；成功为 None
    # 该站从发起到返回（或失败）的耗时。失败站的耗时尤其有诊断价值：
    # 十几秒后才失败的基本是超时，秒失败的多半是认证/解析问题。
    elapsed_ms: int | None = None


class SearchResponse(BaseModel):
    """跨站点聚合搜索的返回结构。

    ``items`` 是所有站点结果的合并列表（每条自带 ``site_id`` / ``site_name``），
    ``sites`` 给出逐站执行状态。前端既能直接铺一个大列表，也能按站点分组、或单独
    提示失败站点。``label`` / ``categories`` 是请求参数的回显，供结果页直接标注
    本次搜索的范围。
    """

    keyword: str
    label: str | None
    categories: list[str]
    total: int
    items: list[TorrentHit]
    sites: list[SiteSearchStatus]


# ---- SSE 流式搜索事件 ---------------------------------------------------------
#
# 流式搜索（GET /search/stream）把一次跨站搜索拆成一串事件推给前端，事件序列固定为：
#
#   start → site_start × N → (site_result | site_error) × N → done
#
# ``site_result`` / ``site_error`` 按各站点**实际完成的先后**到达——快的站点先出结果，
# 前端可以边收边渲染，不再被最慢的站点拖住整页。事件名即 SSE 的 ``event:`` 字段，
# 载荷即 ``data:`` 字段（JSON）。


class SearchStreamSite(BaseModel):
    """事件里的站点标识（``start`` 的站点清单项 / ``site_start`` 的载荷）。"""

    site_id: str
    site_name: str


class SearchStreamStart(BaseModel):
    """``start`` 事件：宣告本次搜索的范围与参与站点，前端据此渲染进度占位。"""

    keyword: str
    label: str | None
    categories: list[str]
    page: int
    sites: list[SearchStreamSite]


class SiteStreamResult(BaseModel):
    """``site_result`` 事件：单个站点搜索成功，携带该站的全部命中结果。"""

    site_id: str
    site_name: str
    count: int
    elapsed_ms: int  # 该站从发起到返回的耗时
    items: list[TorrentHit]


class SiteStreamError(BaseModel):
    """``site_error`` 事件：单个站点搜索失败（认证过期/网络异常等），不影响其它站点。"""

    site_id: str
    site_name: str
    error: str  # 失败原因（可读中文）
    elapsed_ms: int


class SearchStreamDone(BaseModel):
    """``done`` 事件：所有站点均已返回，给出整体汇总（口径同 ``SearchResponse.sites``）。"""

    total: int
    elapsed_ms: int  # 整次搜索耗时（≈ 最慢站点耗时）
    sites: list[SiteSearchStatus]


class CategoryTabItem(BaseModel):
    """标签栏里的内置分类标签；在列表中的位置即展示顺序。

    ``id`` 用枚举类型：未知分类在请求校验阶段即被拒（422），
    存储层无需再防脏数据。
    """

    type: Literal["category"] = "category"
    id: TorrentCategory
    visible: bool


class PresetTabItem(BaseModel):
    """标签栏里的自定义分类标签：命名的「分类组合 × 站点组合」预设。"""

    type: Literal["preset"] = "preset"
    id: str = Field(min_length=1, max_length=64, description="预设标识（创建时生成）")
    name: str = Field(max_length=16, description="展示名称（1~16 字）")
    visible: bool
    categories: list[TorrentCategory] = Field(
        default_factory=list, description="勾选的一级分类；空 = 不限分类"
    )
    site_ids: list[str] = Field(default_factory=list, description="勾选的站点；空 = 全部可用站点")
    poster_mode: bool = Field(
        default=False,
        description="图览模式：用该分类搜索时，结果页默认以图墙展示（结果页可临时切换）",
    )
    skip_history: bool = Field(
        default=False,
        description="无痕搜索：用该分类搜索时不写入搜索历史（隐私敏感场景的开关）",
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("自定义分类的名称不能为空")
        return value


SearchTabItem = Annotated[CategoryTabItem | PresetTabItem, Field(discriminator="type")]


class SearchPreferencesView(BaseModel):
    """搜索偏好视图：永远返回**全量**内置分类（含隐藏项）+ 全部预设，供设置页完整渲染。"""

    tabs: list[SearchTabItem]


class SearchPreferencesUpdate(BaseModel):
    """保存搜索偏好的请求体：整份有序标签列表（缺失的内置分类由后端按默认补齐）。"""

    tabs: list[SearchTabItem]


class SearchHistoryItem(BaseModel):
    """搜索历史的单条记录，供前端渲染「最近搜索」快捷入口。

    ``label`` / ``categories`` / ``site_ids`` 是搜索发生时的快照：点历史重搜时
    按快照原样再搜，预设后来改名/删除都不影响。
    """

    id: int
    keyword: str
    vertical: str  # 搜索垂直：torrent=站点资源 / media=影视条目（豆瓣）
    label: str | None  # 展示名快照（分类中文名/预设名）；None=全部
    categories: list[str]  # 分类组合快照；空=不限分类
    site_ids: list[str]  # 站点组合快照；空=全部站点
    poster_mode: bool  # 图览模式偏好（发起搜索时）；点历史重搜/看快照据此还原展示模式
    search_count: int  # 累计搜索次数
    last_searched_at: datetime  # 最近一次搜索时间（UTC）
    has_snapshot: bool  # 是否已有结果快照（前端据此决定点击进快照预览还是直接重搜）

    @field_serializer("last_searched_at")
    def _serialize_utc(self, value: datetime) -> str:
        """naive UTC → 带 +00:00 的 ISO 串，避免前端按本地时区误解析（同 site.py）。"""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()

    @classmethod
    def from_model(cls, row: SearchHistory) -> SearchHistoryItem:
        """从 ORM 记录构造。``updated_at`` 即最近一次搜索时间（见模型注释）。"""
        assert row.id is not None  # 从库里读出的记录必有主键
        return cls(
            id=row.id,
            keyword=row.keyword,
            vertical=row.vertical,
            label=row.label,
            categories=json.loads(row.categories_json) if row.categories_json else [],
            site_ids=json.loads(row.site_ids_json) if row.site_ids_json else [],
            poster_mode=row.poster_mode,
            search_count=row.search_count,
            last_searched_at=row.updated_at,
            has_snapshot=bool(row.snapshot_json),
        )


class SearchSnapshotView(BaseModel):
    """某条搜索历史的结果快照视图（GET /search/history/{id}/snapshot）。

    结构与 ``SearchResponse`` 同构（items/sites/total 直接复用前端结果页的渲染
    管线），外加历史行的范围回显与快照时间——前端据 ``snapshot_at`` 渲染
    「这是 X 分钟前的快照」提示条。
    """

    history_id: int
    keyword: str
    label: str | None
    categories: list[str]  # 分类组合快照；空=不限分类
    site_ids: list[str]  # 站点组合快照；空=全部站点
    snapshot_at: datetime  # 快照生成时间（UTC）
    total: int
    # 当次搜索的整体耗时；老快照/阻塞版搜索没有该数据时为 None
    elapsed_ms: int | None = None
    items: list[TorrentHit]
    sites: list[SiteSearchStatus]

    @field_serializer("snapshot_at")
    def _serialize_utc(self, value: datetime) -> str:
        """naive UTC → 带 +00:00 的 ISO 串（同 SearchHistoryItem）。"""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()


class MediaSearchSnapshotView(BaseModel):
    """媒体搜索历史的结果快照视图（GET /search/history/{id}/media-snapshot）。

    与 ``SearchSnapshotView``（种子快照）分成两个端点而非塞进同一个联合类型：
    两种快照的载荷结构完全不同（豆瓣条目 vs 种子 + 站点状态），前端本来就从
    历史行的 ``vertical`` 知道该调哪个，分开各自类型干净、互不迁就。
    ``items`` 直接透传快照里的豆瓣条目原始字段（id/source/title/rating/poster_url）。
    """

    history_id: int
    keyword: str
    snapshot_at: datetime  # 快照生成时间（UTC）
    total: int
    items: list[dict]  # 豆瓣条目快照（movieclaw_media.MediaSearchItem 的 dump）

    @field_serializer("snapshot_at")
    def _serialize_utc(self, value: datetime) -> str:
        """naive UTC → 带 +00:00 的 ISO 串（同 SearchHistoryItem）。"""
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
