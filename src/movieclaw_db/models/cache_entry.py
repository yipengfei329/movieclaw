from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel

from movieclaw_db.models.base import utcnow


class CacheEntry(SQLModel, table=True):
    """系统通用持久缓存表：movieclaw_cache 的 SQLite 落盘实现。

    准入边界（与 movieclaw_cache 包的约定一致）：只存**可随时整体删除、可从
    上游重建**的派生数据（豆瓣/TMDB 等上游 API 响应的原始 JSON）。领域数据有
    自己的表，二进制大块走磁盘文件，会话/任务状态是事实源——都不进本表。
    单条 payload 约定不超过 1MB，超过说明用错了地方。

    设计取舍：
    - 只存 ``fetched_at`` 一个时间字段，不存过期时间。新鲜期/可用期策略全部
      由 ``movieclaw_cache.SwrCache`` 按 namespace 在代码里计算——将来调整
      TTL 不需要动数据，也不需要迁移。
    - ``namespace`` 单独成列而非拼进 key：清某个源的缓存、按源统计都是一个
      等值条件，运维干净。豆瓣是首个租户（namespace="douban"）。
    - 不用 TimestampMixin：created/updated 对缓存行没有意义，四列足矣。
    """

    __tablename__ = "cache_entry"

    # 缓存域标识（如 "douban"、"tmdb"），与 cache_key 构成联合主键
    namespace: str = Field(primary_key=True)
    # 域内缓存键，如 "collection:movie_top250:250"、"detail:1292052"
    cache_key: str = Field(primary_key=True)
    # 缓存值的 JSON 文本（原始上游响应，不存解析后的模型）
    payload: str
    # 回源抓取时间（naive UTC）；定期清理任务按它删除超龄行，故建索引
    fetched_at: datetime = Field(default_factory=utcnow, nullable=False, index=True)
