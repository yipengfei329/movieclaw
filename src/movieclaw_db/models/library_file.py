from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Column, ForeignKey, Index, Integer, Text
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class FileSource(StrEnum):
    """library_file.source 的取值。"""

    IMPORTED = "imported"  # 入库管线产出（订阅/手动下载 → 整理器硬链）
    SCANNED = "scanned"  # 存量扫描发现（部署前就在库根路径下的文件）


class LibraryFile(TimestampMixin, table=True):
    """库存台账——"我实际拥有哪个文件"的物理真相源（docs/design/library.md 2.2）。

    设计要点：
    - ``media_item_id`` 可空：**NULL = 未识别**，进"待识别"清单等人工认领
      （宁可待确认，不静默错挂——与订阅低置信度同哲学）；
    - 季/集号沿用 wanted 的约定：电影 (0,0) 哨兵，NOT NULL 保证唯一性可用；
    - 介质规格来自 **ffprobe 对文件本体的探测**（不来自种子名）；探测失败
      保持 NULL（三态铁律）；
    - ``file_path`` 用 Text + 唯一索引（真实媒体路径经常超 255，且它是
      核心去重键——moviebot 的反面教训）；
    - 同条目多版本（1080p 与 2160p 并存）天然支持多行，去重/洗版是 P6 议题；
    - 文件消失不删记录：``missing_since`` 标记，对账任务维护。
    """

    __tablename__ = "library_file"
    __table_args__ = (
        # 库存查询与 wanted 跳过判定的热路径
        Index(
            "ix_library_file_media_unit",
            "media_item_id",
            "season_number",
            "episode_number",
        ),
    )

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
    # 全局身份锚；NULL = 未识别（待识别清单）
    media_item_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("media_item.id", ondelete="SET NULL"), nullable=True),
        description="媒体条目身份锚；NULL=未识别",
    )
    season_number: int = Field(default=0, description="季号；电影=0（哨兵）")
    episode_number: int = Field(default=0, description="集号；电影=0（哨兵）")

    # -- 文件本体 ------------------------------------------------------------
    file_path: str = Field(
        sa_column=Column(Text, nullable=False, unique=True, index=True),
        description="绝对路径（movieclaw 视角）",
    )
    size_bytes: int = Field(default=0, description="文件大小（字节）")
    container: str | None = Field(default=None, description="容器格式（mkv/mp4/…）")

    # -- ffprobe 介质规格（探测失败保持 NULL）-------------------------------
    resolution: str | None = Field(default=None, description="归一化分辨率：2160p/1080p/…")
    video_codec: str | None = Field(default=None, description="视频编码：hevc/h264/av1/…")
    hdr: str | None = Field(default=None, description="HDR 格式：HDR10/HLG/…；SDR 为 NULL")
    bit_depth: int | None = Field(default=None, description="位深：8/10/12")
    duration_seconds: int | None = Field(default=None, description="时长（秒）")
    bit_rate: int | None = Field(default=None, description="总码率（bps）")

    # -- 发布信息（来自文件名解析，enrich 复用）------------------------------
    media_source: str | None = Field(default=None, description="片源：WEB-DL/Blu-ray/…")
    release_group: str | None = Field(default=None, description="发布组")

    # -- 来源与追溯 ----------------------------------------------------------
    source: str = Field(index=True, description="imported（入库管线）/ scanned（存量扫描）")
    site_id: str | None = Field(default=None, description="入库来源站点；scanned 为 NULL")
    torrent_id: str | None = Field(default=None, description="入库来源种子；scanned 为 NULL")

    missing_since: datetime | None = Field(
        default=None, description="对账发现文件消失的时间；NULL=在位"
    )

    # 待识别的原因（扫描识别链失败时记录，给用户"为什么认不出"的解释，
    # 如 TMDB 无法访问 / 解析不出片名）；已识别或人工认领后为 NULL
    unidentified_reason: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
        description="识别失败原因；NULL=已识别或未记录",
    )
