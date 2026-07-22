from __future__ import annotations

from sqlalchemy import JSON, Column
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class Library(TimestampMixin, table=True):
    """媒体库——"我拥有哪些影视内容、放在哪里"的权威定义（docs/design/library.md）。

    L1 阶段的最小形态：库只是"类型 + 落盘根路径"的命名实体，职责是给
    订阅与手动下载提供**入库目标**（save_path 由主根推导）。入库管线的
    transfer_sources、扫描统计等字段随 L2/L3 的消费实现同期加列——
    不预留"存而不用"的配置（moviebot 稻草人配置的教训，见设计文档 1 节）。

    约定：
    - 每库单一类型（movie/tv），命名规范与订阅联通都按类型走；
    - ``root_paths`` 是字符串数组，**第一个为主根**（新入库落主根，
      其余为扩展根，供 L3 盘点对账）——库只有这一套目录体系，对目录的
      用途不做任何假设（它可以同时是下载目录，扫描的完整性检测兜底）；
      "把外部内容搬进库"是独立模块「监听导入」（import_watch）的职责；
    - 每 kind 至多一个默认库（``is_default``），订阅/手动下载不选库时用它。
      不变量由 Repository 维护：同 kind 第一个库自动成为默认；删除默认库时
      默认让给同 kind 剩下最早创建的一个。
    """

    __tablename__ = "library"

    id: int | None = Field(default=None, primary_key=True)
    # 展示名（如"电影库"/"剧集库"/"动漫库"），全局唯一
    name: str = Field(index=True, unique=True, description="库的展示名")
    # movie / tv——创建后不可改（订阅按 kind 挂库，改类型会让既有关联失义）
    kind: str = Field(index=True, description="媒体类型：movie / tv")
    # 根路径数组，第一个为主根；路径指 movieclaw 视角的绝对路径
    root_paths: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
        description="根路径列表（绝对路径，第一个为主根）",
    )
    # 每 kind 至多一个默认库
    is_default: bool = Field(default=False, description="是否为该类型的默认库")

    @property
    def primary_root(self) -> str | None:
        """主根路径（新入库的落点）；未配置任何根时为 None。"""
        return self.root_paths[0] if self.root_paths else None
