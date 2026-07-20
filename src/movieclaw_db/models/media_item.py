from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class MediaItem(TimestampMixin, table=True):
    """统一媒体条目——订阅、资源匹配与媒体库共同的身份锚点。

    定位
    ----
    订阅（期望 E）、种子匹配、媒体库文件台账（库存 H，L2 起）都锚定本表，
    谁也不拥有它——这是"订阅↔库存同锚"的前提（docs/design/library.md 第 0 节）。
    任何入口（TMDB 发现页、豆瓣、未来其他源）的订阅都收敛为本表的一行，
    以 ``(kind, tmdb_id)`` 为唯一锚。本表**不是 TMDB 镜像**，只存"订阅逻辑
    与匹配内核会消费"的最小闭包字段：外部 ID、标题与别名集合、年份、status、
    海报路径。简介/演职员/评分等展示信息走 ``MediaDiscoverService`` 实时接口，
    不落库（详见 docs/design/subscription.md 1.1/1.3）。

    为什么锚定 TMDB：匹配内核依赖英文名/别名集合（种子以英文场景命名）、
    季集结构、每集播出日期，三者只有 TMDB 免费且完整提供。豆瓣条目在订阅
    创建时收敛到本表（douban_id 留存为来源与精确匹配信号），不允许创建
    无 tmdb_id 的"无锚条目"。

    三态铁律与全表约定同 ``SiteTorrent``：可缺失字段 NULL=未知，
    语义空值用空串/空列表。
    """

    __tablename__ = "media_item"
    __table_args__ = (
        # 同一类型下 TMDB ID 唯一——ensure_media_item 幂等复用的依据
        UniqueConstraint("kind", "tmdb_id", name="uq_media_item_kind_tmdb"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # -- 身份锚（唯一键，永不为空）-----------------------------------------
    # 存 MediaKind 的字符串值（"movie"/"tv"），db 层不反向依赖 media 层枚举
    # （同 site_torrent.category 的处理方式）
    kind: str = Field(index=True, description="媒体类型：movie / tv")
    tmdb_id: int = Field(description="TMDB 条目 ID（锚）")

    # -- 外部 ID：与 site_torrent 详情层精确匹配的桥 ------------------------
    # imdb_id / douban_id 与种子富化带回的同名字段精确相等时，是比标题匹配
    # 可靠得多的命中信号（匹配内核的第一优先级）
    imdb_id: str | None = Field(default=None, index=True, description="IMDb ID；无/未知为 NULL")
    douban_id: str | None = Field(
        default=None, index=True, description="豆瓣 ID；非豆瓣入口且未知为 NULL"
    )

    # -- 标题与匹配素材 ------------------------------------------------------
    title: str = Field(description="主展示标题（zh-CN 优先）")
    original_title: str = Field(description="原始语言标题")
    year: int | None = Field(
        default=None, description="上映/首播年份；NULL=未知（匹配的硬约束之一）"
    )
    # 别名集合存**原样文本**（TMDB alternative_titles/translations + 豆瓣标题），
    # 仅精确去重；归一化（大小写/全半角/繁简）是匹配内核的职责——规则会进化，
    # 数据不动、规则动，避免内核升级时全量重写数据
    aliases: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="匹配用别名集合（原样文本，精确去重）",
    )

    # -- 生命周期 ------------------------------------------------------------
    # 存 TMDB status 原值（Released / Returning Series / Ended / Canceled…），
    # 元数据刷新任务据此分档决定刷新间隔
    status: str | None = Field(default=None, description="TMDB status 原值；NULL=未知")

    # -- 展示（仅海报路径，前端经 image-proxy 拼接）-------------------------
    poster_path: str | None = Field(default=None, description="TMDB 海报相对路径")
    backdrop_path: str | None = Field(default=None, description="TMDB 宽幅剧照相对路径")

    # -- 元数据刷新台账（仿 SiteSyncCursor 的 tick 模式）--------------------
    metadata_refreshed_at: datetime | None = Field(
        default=None, description="上次成功刷新元数据；NULL=建档后未刷过"
    )
    # NULL=立即到期：建档后首个刷新 tick 即处理，由刷新任务按 status 分档重排
    next_refresh_at: datetime | None = Field(
        default=None, description="下次刷新到期时刻；NULL=立即到期"
    )


class MediaSeason(TimestampMixin, table=True):
    """剧集条目的季——按季订阅的骨架（仅 kind=tv 的条目有行）。

    集不单独建表：``wanted_item`` 用 (season_number, episode_number) 数字引用
    而非外键，单剧只有几百集，所有查询入口都是"某订阅的某几季"，逐季读
    ``episodes`` JSON 足够（决策与代价见 docs/design/subscription.md 1.6）。

    ``episodes`` 元素形如 ``{"episode_number": 1, "name": "...", "air_date": "2026-01-05"}``，
    air_date 为 ISO 日期字符串或 null（未定档）。每集播出日期是 wanted 生成
    （补缺失=已播集、只追未来=订阅后播出的集）与新集追加的判断依据。
    """

    __tablename__ = "media_season"
    __table_args__ = (
        # 同一条目内季号唯一——元数据刷新按 (条目, 季号) upsert
        UniqueConstraint("media_item_id", "season_number", name="uq_media_season_item_season"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # 条目删除时季随之级联删除（engine 已开启 SQLite 外键约束）
    media_item_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("media_item.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="所属媒体条目",
    )
    # 0=特别季（Specials），允许存在但默认不参与订阅
    season_number: int = Field(description="季号；0=特别季")

    name: str = Field(default="", description="季名；语义空值为空串")
    air_date: date | None = Field(default=None, description="该季首播日期；NULL=未定档/未知")
    episode_count: int | None = Field(default=None, description="TMDB 宣称的集数；NULL=未知")
    episodes: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="集列表 JSON：[{episode_number, name, air_date}]；空列表=暂无集数据",
    )
