"""订阅接口的请求/响应模型。

命名沿用项目 API 惯例的 snake_case；时间字段输出前补 UTC 时区标记
（库内 naive UTC，理由见 schemas.site.ConfiguredSite）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_serializer

from movieclaw_db.models import (
    MediaItem,
    MediaSeason,
    RuleSet,
    Subscription,
    SubscriptionActivity,
    WantedItem,
)
from movieclaw_db.models.base import utcnow
from movieclaw_media.library import ResolveCandidate
from movieclaw_media.models import MediaKind


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


class PreparePayload(BaseModel):
    """订阅弹层打开时的预检请求。

    - source=tmdb：带 kind + tmdb_id（发现页/详情页入口）；
    - source=douban：带 kind + title（豆瓣入口，year/douban_id 尽量带上，
      收敛精度更高）。
    """

    source: Literal["tmdb", "douban"] = "tmdb"
    kind: MediaKind
    tmdb_id: int | None = None
    title: str | None = Field(default=None, description="豆瓣入口：豆瓣标题")
    year: int | None = Field(default=None, description="豆瓣入口：年份（可缺）")
    douban_id: str | None = Field(default=None, description="豆瓣入口：豆瓣条目 ID")


class MediaBrief(BaseModel):
    """弹层与列表共用的条目摘要。"""

    media_item_id: int
    kind: MediaKind
    tmdb_id: int
    douban_id: str | None
    title: str
    original_title: str
    year: int | None
    poster_url: str | None = Field(description="完整海报 URL（按配置的图床基址拼好）")
    status: str | None

    @classmethod
    def from_model(cls, item: MediaItem) -> MediaBrief:
        from movieclaw_api.core.config import get_settings

        poster_url = None
        if item.poster_path:
            base = get_settings().tmdb_image_base_url.rstrip("/")
            poster_url = f"{base}/w500{item.poster_path}"
        return cls(
            media_item_id=item.id,  # type: ignore[arg-type]  # 落库后必有主键
            kind=MediaKind(item.kind),
            tmdb_id=item.tmdb_id,
            douban_id=item.douban_id,
            title=item.title,
            original_title=item.original_title,
            year=item.year,
            poster_url=poster_url,
            status=item.status,
        )


class SeasonOverview(BaseModel):
    """弹层季选择器的一行：季号 + 播出进度 + 库存进度。"""

    season_number: int
    name: str
    air_date: date | None
    episode_count: int | None
    aired_count: int = Field(description="已播集数（air_date<=今天）")
    owned_count: int = Field(default=0, description="媒体库已有的集数（库存 H）")

    @classmethod
    def from_row(
        cls, season: MediaSeason, *, owned_units: set[tuple[int, int]] | None = None
    ) -> SeasonOverview:
        today = utcnow().date()
        aired = 0
        for episode in season.episodes:
            raw = episode.get("air_date")
            try:
                if raw and date.fromisoformat(raw) <= today:
                    aired += 1
            except ValueError:
                continue
        owned = 0
        if owned_units:
            owned = sum(1 for s, _e in owned_units if s == season.season_number)
        return cls(
            season_number=season.season_number,
            name=season.name,
            air_date=season.air_date,
            episode_count=season.episode_count,
            aired_count=aired,
            owned_count=owned,
        )


class ResolveCandidateView(BaseModel):
    """豆瓣收敛歧义时的确认候选。"""

    tmdb_id: int
    title: str
    original_title: str
    year: int | None
    poster_url: str | None

    @classmethod
    def from_model(cls, c: ResolveCandidate) -> ResolveCandidateView:
        from movieclaw_api.core.config import get_settings

        poster_url = None
        if c.poster_path:
            base = get_settings().tmdb_image_base_url.rstrip("/")
            poster_url = f"{base}/w342{c.poster_path}"
        return cls(
            tmdb_id=c.tmdb_id,
            title=c.title,
            original_title=c.original_title,
            year=c.year,
            poster_url=poster_url,
        )


class PrepareView(BaseModel):
    """预检结果三态：ready 可直接渲染弹层；ambiguous 先让用户选候选；
    not_found 提示该条目暂无法订阅。"""

    status: Literal["ready", "ambiguous", "not_found"]
    media: MediaBrief | None = None
    seasons: list[SeasonOverview] = Field(default_factory=list)
    existing_subscription_id: int | None = Field(
        default=None, description="该条目已有订阅时给出，前端展示「已订阅」态"
    )
    movie_owned: bool = Field(
        default=False, description="电影：媒体库里已有本片（弹层提示，不拦订阅）"
    )
    candidates: list[ResolveCandidateView] = Field(default_factory=list)


class DispatchPreviewView(BaseModel):
    """投递路由预检（订阅弹窗选库时的即时提示）。

    与真实投递的三级兜底 + 映射守门同源判定，把"下载完成后能不能进库"
    这个问题在订阅那一刻就回答掉，而不是等投递失败/落点告警才暴露。
    """

    mode: Literal["watch", "inplace", "downloader_default"] = Field(
        description="投递路由：监听导入目录 / 直接下载进库 / 下载器默认目录"
    )
    path: str | None = Field(default=None, description="movieclaw 视角的投递基底目录")
    library_name: str | None = None
    downloader_name: str | None = None
    ok: bool = Field(description="按当前配置投递能否顺利入库")
    warning: str | None = Field(default=None, description="不 ok 时的中文指引")


# ---------------------------------------------------------------------------
# 订阅 CRUD
# ---------------------------------------------------------------------------


class SubscriptionCreatePayload(BaseModel):
    kind: MediaKind
    tmdb_id: int
    selected_seasons: list[int] = Field(default_factory=list)
    follow_future: bool = False
    rule_set_id: int | None = Field(default=None, description="缺省用默认规则组")
    library_id: int | None = Field(default=None, description="入库目标库；缺省用该类型默认库")
    douban_id: str | None = Field(default=None, description="豆瓣入口时带上，留存来源身份")


class SubscriptionUpdatePayload(BaseModel):
    selected_seasons: list[int] | None = None
    follow_future: bool | None = None
    rule_set_id: int | None = None
    library_id: int | None = Field(default=None, description="换入库目标库；缺省不变")


class SubscriptionPausePayload(BaseModel):
    paused: bool


class ProgressView(BaseModel):
    """列表页进度：total = 工单总数，wanted 子集是缺口，imported 是已入库终态。"""

    total: int
    wanted: int
    grabbed: int
    downloaded: int
    imported: int


class SubscriptionView(BaseModel):
    id: int
    media: MediaBrief
    status: str
    selected_seasons: list[int]
    follow_future: bool
    rule_set_id: int
    library_id: int | None = Field(description="入库目标库；null=该类型默认库")
    progress: ProgressView
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        return _iso_utc(value)

    @classmethod
    def from_model(
        cls, sub: Subscription, item: MediaItem, counts: dict[str, int]
    ) -> SubscriptionView:
        wanted = counts.get("wanted", 0)
        grabbed = counts.get("grabbed", 0)
        downloaded = counts.get("downloaded", 0)
        imported = counts.get("imported", 0)
        return cls(
            id=sub.id,  # type: ignore[arg-type]
            media=MediaBrief.from_model(item),
            status=sub.status,
            selected_seasons=list(sub.selected_seasons),
            follow_future=sub.follow_future,
            rule_set_id=sub.rule_set_id,
            library_id=sub.library_id,
            progress=ProgressView(
                total=wanted + grabbed + downloaded + imported,
                wanted=wanted,
                grabbed=grabbed,
                downloaded=downloaded,
                imported=imported,
            ),
            created_at=sub.created_at,
            updated_at=sub.updated_at,
        )


class WantedView(BaseModel):
    id: int
    season_number: int
    episode_number: int
    status: str
    air_date: date | None
    priority: int
    next_search_at: datetime | None
    search_attempts: int
    last_search_at: datetime | None
    grabbed_at: datetime | None
    downloaded_at: datetime | None
    imported_at: datetime | None

    @field_serializer(
        "next_search_at", "last_search_at", "grabbed_at", "downloaded_at", "imported_at"
    )
    def _serialize_utc(self, value: datetime | None) -> str | None:
        return _iso_utc(value)

    @classmethod
    def from_model(cls, w: WantedItem) -> WantedView:
        return cls(
            id=w.id,  # type: ignore[arg-type]
            season_number=w.season_number,
            episode_number=w.episode_number,
            status=w.status,
            air_date=w.air_date,
            priority=w.priority,
            next_search_at=w.next_search_at,
            search_attempts=w.search_attempts,
            last_search_at=w.last_search_at,
            grabbed_at=w.grabbed_at,
            downloaded_at=w.downloaded_at,
            imported_at=w.imported_at,
        )


class SubscriptionDetailView(SubscriptionView):
    wanted: list[WantedView] = Field(default_factory=list)

    @classmethod
    def from_detail(
        cls,
        sub: Subscription,
        item: MediaItem,
        wanted_rows: list[WantedItem],
    ) -> SubscriptionDetailView:
        counts: dict[str, int] = {}
        for w in wanted_rows:
            counts[w.status] = counts.get(w.status, 0) + 1
        base = SubscriptionView.from_model(sub, item, counts)
        return cls(
            **base.model_dump(),
            wanted=[WantedView.from_model(w) for w in wanted_rows],
        )


class ActivityView(BaseModel):
    """活动时间线的一条记录：message 已是完整中文句子，前端直接展示。"""

    id: int
    type: str
    message: str
    payload: dict
    created_at: datetime

    @field_serializer("created_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        return _iso_utc(value)

    @classmethod
    def from_model(cls, row: SubscriptionActivity) -> ActivityView:
        return cls(
            id=row.id,  # type: ignore[arg-type]
            type=row.type,
            message=row.message,
            payload=dict(row.payload),
            created_at=row.created_at,
        )


# ---------------------------------------------------------------------------
# 规则组
# ---------------------------------------------------------------------------


class RuleSetPayload(BaseModel):
    name: str
    spec: dict = Field(default_factory=dict, description="RuleSetSpec 形态的 JSON")


class RuleSetView(BaseModel):
    id: int
    name: str
    is_default: bool
    spec: dict

    @classmethod
    def from_model(cls, row: RuleSet) -> RuleSetView:
        return cls(
            id=row.id,  # type: ignore[arg-type]
            name=row.name,
            is_default=row.is_default,
            spec=dict(row.spec),
        )
