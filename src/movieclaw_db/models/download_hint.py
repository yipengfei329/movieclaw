from __future__ import annotations

from sqlalchemy import Column, Text
from sqlmodel import Field

from movieclaw_db.models.base import TimestampMixin


class DownloadHint(TimestampMixin, table=True):
    """下载识别线索：把种子**副标题**锚定到提交下载时推导的入库目录。

    动机：拼音命名种子（如「Qiang Qiang Shi Yi」实为《锵锵拾遗》）仅靠
    文件/目录名在 TMDB 无解，但站点副标题里几乎总有中文片名与「全N集」。
    提交下载时目录已定、副标题在手，落一条线索；下载完成后扫描器识别
    该目录下的文件时取回副标题，作备选查询词与集数佐证（library_resolve）。

    - ``save_path`` 唯一：同一条目目录重复提交（换版本重下）覆盖更新即可；
      只在推导出**条目级**目录时写入——锚到库主根会波及根下所有文件；
    - 只存副标题原文，中文名/集数在扫描时解析（enrich 演进无需回填）。
    """

    __tablename__ = "download_hint"

    id: int | None = Field(default=None, primary_key=True)
    save_path: str = Field(
        sa_column=Column(Text, nullable=False, unique=True, index=True),
        description="提交下载时推导的条目目录（绝对路径）",
    )
    subtitle: str = Field(
        sa_column=Column(Text, nullable=False),
        description="站点副标题原文",
    )
    site_id: str | None = Field(default=None, description="来源站点（追溯用）")
