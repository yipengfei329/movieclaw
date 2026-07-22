from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin, utcnow


class IngestStatus(StrEnum):
    """ingest_entry.status 的取值。"""

    IMPORTED = "imported"  # 已搬进库（可能带部分文件的跳过说明）
    FAILED = "failed"  # 处理失败（识别不出/探测失败/搬运出错），等待重试
    SKIPPED = "skipped"  # 非影视内容（无视频文件），永久跳过


class IngestEntry(TimestampMixin, table=True):
    """下载监听目录条目的处理台账。

    条目 = 监听目录下的一个顶层文件/目录（一次下载任务的产物）。台账的
    职责是**幂等与可追溯**：源文件搬运后仍留在监听目录（硬链保种/复制
    留档），没有这张表每轮扫描都会重复处理同一批条目。

    重试语义靠 ``fingerprint``（条目全树的 大小:文件数:mtime 摘要）：
    - 指纹不变：imported/skipped 永久跳过，failed 按时间退避重试；
    - 指纹变化：任何状态都重新处理——覆盖"下载在继续（此前误判）"与
      "季包边下边补集"两种场景，已入库的文件靠目标去重天然幂等。
    """

    __tablename__ = "ingest_entry"

    id: int | None = Field(default=None, primary_key=True)

    library_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("library.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="归属库",
    )
    entry_path: str = Field(
        sa_column=Column(Text, nullable=False, unique=True, index=True),
        description="条目绝对路径（监听目录的顶层文件/目录）",
    )
    fingerprint: str = Field(description="处理时的条目指纹（大小:文件数:mtime）")
    status: str = Field(description="imported / failed / skipped")
    message: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="处理结论（中文，含跳过/失败原因）",
    )
    imported_count: int = Field(default=0, description="本条目累计入库的文件数")
    attempted_at: datetime = Field(
        default_factory=utcnow, description="最近一次处理时间（failed 的退避重试依据）"
    )
