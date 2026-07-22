from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin, utcnow


class TorrentSource(StrEnum):
    """本行数据的采集来源——用于判断字段完整度与刷新策略。

    不同来源能提供的字段范围不同，这直接决定「刷新时哪些字段可信」：
    - ``LIST``：浏览页 / API 列表。字段最全，易变层（做种数、促销）可信。
    - ``RSS``：RSS 源。结构上缺 seeders/free/category，仅静态层可信，
      因此从 RSS 来的观测**不应写易变层**（交给消费方置 None）。
    - ``SEARCH``：关键词搜索兜底。字段范围同 LIST。
    - ``DETAIL``：详情页富化。用于补 imdb/douban 等详情层字段。
    """

    LIST = "list"
    RSS = "rss"
    SEARCH = "search"
    DETAIL = "detail"


class SiteTorrent(TimestampMixin, table=True):
    """PT 站点种子的本地快照索引——高频规则检索的数据底座。

    定位
    ----
    从「用户添加站点」那一刻(t0)起，前向跟随站点最新发布：把种子的**静态信息**
    落库、**易变信息**顺手保鲜。规则匹配直接查本表，从而把对 PT 站的高频访问
    降到最低。它**不是全站镜像**——早于 t0 的历史内容不在此表，需要时由实时
    搜索兜底回填（见 ``TorrentSource.SEARCH``）。

    空值 / 异常 / 默认的三态铁律
    ---------------------------
    每个可能缺失的字段区分三种状态，绝不让「未知」和「业务零值」撞车：
    - **有值**：观测到真实值 → 存该值；
    - **语义零值**：确实为 0 / 正常 / 无促销 → 存 0 / 1.0 / False；
    - **未知**：这次没解析到 / 该来源不提供 → **存 NULL**。

    因此**易变层字段一律 nullable**：``seeders=NULL`` 表示「没观测到」，
    ``seeders=0`` 表示「真的没人做种」，二者语义不可混淆。写入策略见
    ``TorrentRepository``：首次入库缺则 NULL、``title`` 缺则整行拒绝；刷新时
    只覆盖「这次真正解析成功」的字段，解析失败的保留旧值、绝不用 NULL 覆盖。
    """

    __tablename__ = "site_torrent"
    __table_args__ = (
        # 同一站点内 torrent_id 唯一——upsert 的冲突键
        UniqueConstraint("site_id", "torrent_id", name="uq_site_torrent_site_tid"),
    )

    id: int | None = Field(default=None, primary_key=True)

    # -- 身份标识（唯一键，永不为空）--------------------------------------
    site_id: str = Field(index=True, description="站点标识")
    torrent_id: str = Field(description="站点内种子 ID（字符串，兼容非数字 ID）")

    # -- 静态层：首次入库确定，之后基本不变 --------------------------------
    # title 是硬不变量：解析不到标题的行在首次入库时直接拒绝，表里不存在空标题行
    title: str = Field(index=True, description="主标题")
    # 展示类文本：语义空值是空串而非 NULL（前端可直接拼接）
    subtitle: str = Field(default="", description="副标题 / 小描述")
    # 分类解析不到时为 NULL（未知），不要默认塞 OTHER 掩盖问题。
    # 存 TorrentCategory 的字符串值（而非直接引用枚举），避免 db 层反向依赖 tracker 层
    category: str | None = Field(
        default=None, index=True, description="应用级一级分类值；NULL=未知"
    )
    site_category_id: str | None = Field(default=None, description="站点原始分类 ID")
    # 大小：真实种子不会是 0 字节，解析失败一律 NULL；另存原始文本便于排错
    size_bytes: int | None = Field(default=None, description="字节数；NULL=未解析到")
    size_text: str | None = Field(default=None, description="原始大小文本，仅展示 / 排错")
    # 发布时间：解析失败为 NULL（宁可未知也不塞假时间污染高水位判断）
    publish_time: datetime | None = Field(
        default=None, index=True, description="种子发布时间；NULL=未知"
    )
    uploader: str = Field(default="", description="发布者；语义空值为空串")

    # -- 易变层：每次触达刷新；三态，全部 nullable ------------------------
    seeders: int | None = Field(default=None, description="做种数；NULL=未观测，0=真实为0")
    leechers: int | None = Field(default=None, description="下载数；NULL=未观测")
    snatched: int | None = Field(default=None, description="完成数；NULL=未观测")
    # 促销以 download_volume_factor 为事实来源；is_free 是其派生索引列
    download_volume_factor: float | None = Field(
        default=None, description="下载系数 0/0.3/0.5/1.0；NULL=未观测"
    )
    upload_volume_factor: float | None = Field(default=None, description="上传系数；NULL=未观测")
    # 派生列：便于「筛当前免费」快速查询；由 upsert 层依 factor 维护，不独立赋值
    is_free: bool | None = Field(
        default=None, index=True, description="是否全免；派生自 factor，NULL=未知"
    )
    # 促销截止：NULL 有两义——无促销 或 未知；须配合 is_free 一起解读。
    # 注意：M-Team「长期免费无截止」的哨兵 datetime.max 必须在写入前归一化成 NULL
    free_deadline: datetime | None = Field(
        default=None, description="促销截止时间；NULL=无促销 / 长期 / 未知"
    )

    # -- H&R 考核：三态——True=有考核，False=站点标注无考核，NULL=站点不提供/未适配。
    # 与促销不同，缺席不代表没有考核，绝不能塌缩成 False（见 tracker 层同名字段注释）
    hit_and_run: bool | None = Field(default=None, description="是否 H&R 考核种子；NULL=未知")

    # -- 扩充层：movieclaw_enrich 从标题/副标题推导的结构化属性 -------------
    # attrs 存 TorrentAttrs 的 JSON（exclude_defaults，空产出为 {}）；
    # enrich_version 记录产出它的提取器版本，启动时的重算任务据此找出过期行。
    # NULL/NULL = 从未扩充过（老数据），{} + 版本号 = 扩充过但没提取到任何字段。
    attrs: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
        description="扩充属性 JSON；NULL=尚未扩充",
    )
    enrich_version: int | None = Field(
        default=None, description="产出 attrs 的提取器版本；NULL=尚未扩充"
    )

    # -- 详情层：仅 DETAIL 来源填充，列表刷新绝不可覆盖为空 ----------------
    imdb_id: str | None = Field(default=None, index=True, description="IMDb ID；未富化为 NULL")
    douban_id: str | None = Field(default=None, description="豆瓣 ID；未富化为 NULL")

    # -- 链接 --------------------------------------------------------------
    detail_url: str | None = Field(default=None, description="详情页 URL")
    # 下载入口：NexusPHP 存下载 URL；M-Team 存种子 ID（与 tracker 层约定一致）
    download_url: str | None = Field(default=None, description="下载入口")

    # -- 台账：新鲜度与来源，供下载决策与排错 ------------------------------
    # created_at（来自 Mixin）即「首次入库时间」，不再单设 first_seen
    source: TorrentSource = Field(description="本行当前数据的采集来源")
    # 最近一次在列表里「看到」它——证明它还在榜上，与内容是否变化无关
    last_seen_at: datetime = Field(default_factory=utcnow, description="最近一次被列表观测到")
    # 最近一次成功刷新易变层的时间——下载决策据此判断 free/seeders 是否过期
    volatile_refreshed_at: datetime | None = Field(
        default=None, description="易变层最近成功刷新时间；NULL=从未拿到过易变数据"
    )
    # 最近一次详情页富化时间；NULL=从未补过详情
    detail_enriched_at: datetime | None = Field(default=None, description="详情层最近富化时间")


class SiteSyncCursor(TimestampMixin, table=True):
    """每站一条的同步游标：记录跟踪起点、进度与观测到的发布速率。

    - ``tracking_since`` 是 t0——回补翻页的**硬下限**，永不回溯到它之前。
    - ``last_new_count`` / ``last_full_page`` 是自适应轮询节奏的输入：新增多且
      首页全是新的，说明该站发布快、可能有漏，应调密节奏并触发回补。
    - ``last_error`` 记录面向部署者的可读失败原因，契合项目「非开发者也要看得懂
      错误」的原则。
    """

    __tablename__ = "site_sync_cursor"

    id: int | None = Field(default=None, primary_key=True)
    site_id: str = Field(index=True, unique=True, description="站点标识")

    # 跟踪起点 t0：用户添加站点即写入，是回补翻页的时间下限
    tracking_since: datetime = Field(default_factory=utcnow, description="开始跟踪时间(t0)")
    # 已知最新种子的发布时间 / ID——增量停止条件的参考（真正判重仍按 torrent_id 查表）
    newest_publish_time: datetime | None = Field(default=None, description="已知最新发布时间")
    newest_torrent_id: str | None = Field(default=None, description="已知最新种子 ID")

    last_sync_at: datetime | None = Field(default=None, description="上次同步完成时间")
    # 上次「成功」同步的时间——站点宕机期间 last_sync_at 仍会推进（每次尝试都算），
    # 而这个字段停在最后一次拿到数据的时刻，供展示「已多久没同步成功」
    last_success_at: datetime | None = Field(
        default=None, description="上次同步成功时间；NULL=从未成功"
    )
    # 上次同步新增条数——驱动自适应节奏
    last_new_count: int | None = Field(default=None, description="上次同步新增种子数")
    # 上次同步首页是否「全是新的」——为真意味着两次间隔太长、发生了漏种
    last_full_page: bool | None = Field(default=None, description="上次首页是否全为新种")
    # 上次同步失败原因（可读文本）；成功为 NULL
    last_error: str | None = Field(default=None, description="上次同步失败原因；成功为 NULL")
    # 连续失败次数——驱动失败退避（指数放疏重试节奏），成功即清零
    consecutive_failures: int = Field(default=0, description="连续同步失败次数；成功清零")

    # -- 自适应轮询节奏（per-site cadence，供全局 tick 判断到期）-----------
    # 该站当前轮询间隔（秒）：由同步任务按发布速率自适应升降，夹在 [min, max]。
    # 默认 900=15 分钟，作为新站的起始节奏。
    sync_interval_seconds: int = Field(
        default=900, description="该站当前轮询间隔（秒），自适应结果"
    )
    # 下次到期时刻：tick 据此判断「这个站该同步了吗」。
    # NULL 视为「立即到期」——用户刚加站点即刻首刷正是靠它。
    next_sync_at: datetime | None = Field(
        default=None, description="下次同步到期时刻；NULL=立即到期"
    )
