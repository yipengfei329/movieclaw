"""媒体库接口的请求/响应模型。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_serializer

from movieclaw_db.models.library import Library
from movieclaw_media.models import MediaKind


class LibraryPayload(BaseModel):
    """创建/更新库的请求体。kind 仅创建时生效，更新时忽略（创建后不可改）。"""

    name: str = Field(description="库的展示名（全局唯一）")
    kind: MediaKind = Field(description="媒体类型：movie / tv")
    root_paths: list[str] = Field(
        description="根路径列表（绝对路径），第一个为主根——新入库落在这里"
    )


class LibraryStats(BaseModel):
    """库存统计（library_file 聚合，查询时现算——L1 曾用订阅数占位，L3 起是真库存）。"""

    item_count: int = Field(default=0, description="已识别的媒体条目数")
    file_count: int = Field(default=0, description="在账文件总数（含待识别）")
    total_size_bytes: int = Field(default=0, description="文件总大小（字节）")
    unidentified_count: int = Field(default=0, description="待识别文件数")


class LibraryView(BaseModel):
    id: int
    name: str
    kind: MediaKind
    root_paths: list[str]
    primary_root: str | None = Field(description="主根路径（root_paths 第一项）")
    is_default: bool
    stats: LibraryStats = Field(default_factory=LibraryStats)
    scanning: bool = Field(default=False, description="是否正在扫描")
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()

    @classmethod
    def from_model(
        cls,
        row: Library,
        *,
        stats: LibraryStats | None = None,
        scanning: bool = False,
    ) -> LibraryView:
        return cls(
            id=row.id,  # type: ignore[arg-type]  # 落库后必有主键
            name=row.name,
            kind=MediaKind(row.kind),
            root_paths=list(row.root_paths),
            primary_root=row.primary_root,
            is_default=row.is_default,
            stats=stats or LibraryStats(),
            scanning=scanning,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class LibraryItemView(BaseModel):
    """库内一个媒体条目的库存聚合（单库海报墙的一格）。"""

    media_item_id: int
    kind: MediaKind
    tmdb_id: int
    title: str
    year: int | None
    poster_url: str | None
    file_count: int
    total_size_bytes: int
    # 在库的季号列表（电影为空）；集数 = 去重的 (季,集) 单元数
    seasons: list[int]
    episode_count: int
    # 去重的介质规格标签（如 ["2160p","1080p"]），探测不到为空
    resolutions: list[str]
    missing_count: int = Field(description="标记 missing 的文件数（>0 时前端提示）")


class UnidentifiedFileView(BaseModel):
    """待识别清单的一行。"""

    id: int
    library_id: int
    library_name: str
    file_path: str
    size_bytes: int
    season_number: int
    episode_number: int


class ClaimPayload(BaseModel):
    """人工认领：把待识别文件挂到 TMDB 条目。"""

    tmdb_id: int
    season_number: int = 0
    episode_number: int = 0


class ScanResultView(BaseModel):
    """扫描启动响应。"""

    started: bool
    message: str
