from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """返回当前 UTC 时间（naive，不带 tzinfo）。

    为什么用 naive UTC 而非带时区时间：SQLite 没有原生的带时区日期类型，
    ``DateTime(timezone=True)`` 写入后读回会丢失 tzinfo，形成"写 aware / 读 naive"
    的不一致，一旦拿去做时间比较就会抛 "can't compare offset-naive and
    offset-aware datetimes"。因此本项目约定：**数据库里一律存 UTC 朴素时间**，
    需要展示本地时间时在展示层统一转换。
    """
    return datetime.now(UTC).replace(tzinfo=None)


class TimestampMixin(SQLModel):
    """时间戳混入基类。

    所有业务表都应包含创建时间与更新时间，便于排查问题、做缓存过期判断等。
    - created_at：记录首次落库时间，创建后不再变化。
    - updated_at：每次更新记录时刷新（由 Repository 层在写入前赋值；SQLite 无
      数据库级 ON UPDATE 触发，放在应用层维护最直观、可控）。

    时间统一为 UTC 朴素时间，理由见 ``utcnow`` 的说明。
    """

    created_at: datetime = Field(default_factory=utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=utcnow, nullable=False)
