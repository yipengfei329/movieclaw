from __future__ import annotations

from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class ImportWatch(TimestampMixin, table=True):
    """监听导入规则——媒体库之上的独立功能：源目录 → 目标库的搬运配置。

    设计定位（与媒体库解耦的关键）：媒体库只有一套目录体系（根路径），
    只做盘点与守护、不承载"内容从哪来"的假设；把下载完成的内容搬进库
    是本模块的职责。每条规则声明：监听哪个源目录、完成的内容硬链/复制
    到哪个库（落库主根，识别按库类型走）。

    约束（写入侧校验，import_watch_config）：
    - 源目录全局唯一，且不得与**任何**库的根路径前缀重叠——落在库根下
      会被那个库当存量扫走，双头管理必乱；
    - 策略 hardlink 时保存即做同盘检测（源与目标库主根 st_dev 比对）。
    """

    __tablename__ = "import_watch"

    id: int | None = Field(default=None, primary_key=True)

    source_path: str = Field(
        sa_column=Column(Text, nullable=False, unique=True, index=True),
        description="监听的源目录（movieclaw 视角的绝对路径）",
    )
    strategy: str = Field(description="搬运策略：hardlink（需同盘）/ copy（可跨盘）")
    library_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("library.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        description="目标媒体库（导入落其主根，识别按其类型）",
    )
