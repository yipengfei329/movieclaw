"""服务器目录浏览接口的响应模型（前端目录选择器数据源）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FsEntry(BaseModel):
    """当前目录下的一个子目录。"""

    name: str = Field(description="目录名")
    path: str = Field(description="绝对路径")


class FsBrowseView(BaseModel):
    """一次目录浏览的结果：当前位置 + 上级 + 子目录列表。"""

    path: str = Field(description="当前目录的绝对路径（已规范化）")
    parent: str | None = Field(description="上级目录路径；已在根目录时为 null")
    entries: list[FsEntry] = Field(description="子目录列表（只含目录，按名称排序）")
