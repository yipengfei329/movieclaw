"""媒体库接口的请求/响应模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

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
    missing_count: int = Field(default=0, description="标记 missing 的文件数（缺失清单入口）")


class LastScanView(BaseModel):
    """最近一次扫描的结论——扫描常毫秒级结束，前端靠它给用户"点了有反应"的反馈。"""

    finished_at: datetime
    scanned: int = Field(description="本轮新入账文件数")
    identified: int
    unidentified: int
    marked_missing: int = Field(description="本轮标记丢失的文件数")
    errors: list[str] = Field(default_factory=list)

    @field_serializer("finished_at")
    def _serialize_utc(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()


class ScanProgressView(BaseModel):
    """进行中扫描的实时进度（前端在库封面上画进度环）。"""

    processed: int
    total: int


class LibraryView(BaseModel):
    id: int
    name: str
    kind: MediaKind
    root_paths: list[str]
    primary_root: str | None = Field(description="主根路径（root_paths 第一项）")
    is_default: bool
    stats: LibraryStats = Field(default_factory=LibraryStats)
    scanning: bool = Field(default=False, description="是否正在扫描")
    scan_progress: ScanProgressView | None = Field(default=None, description="扫描实时进度")
    last_scan: LastScanView | None = Field(default=None, description="最近一次扫描结论")
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
        scan_progress: ScanProgressView | None = None,
        last_scan: LastScanView | None = None,
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
            scan_progress=scan_progress,
            last_scan=last_scan,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


# TMDB status 原值 → 播出状态两分类（剧集海报悬浮操作的判断依据）。
# 映射外的未知值不猜——返回 None，前端降级为静态「已入库」标识，
# 不给出错误的「订阅追新 / 补齐缺集」入口。
_AIRING_STATUSES = frozenset({"Returning Series", "In Production", "Planned", "Pilot"})
_ENDED_STATUSES = frozenset({"Ended", "Canceled"})


def derive_air_status(status: str | None) -> Literal["airing", "ended"] | None:
    """剧集 TMDB status 原值收敛为 airing（还会有新集）/ ended（不会再有）。"""
    if status in _AIRING_STATUSES:
        return "airing"
    if status in _ENDED_STATUSES:
        return "ended"
    return None


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
    # 剧集海报悬浮操作的两个判断依据（前端三分支：在播→订阅追新 /
    # 完结缺集→补齐缺集 / 完结齐全或电影→已入库）
    air_status: Literal["airing", "ended"] | None = Field(
        default=None,
        description="剧集播出状态：airing=在播 / ended=完结；电影或状态未知为 NULL",
    )
    missing_episode_count: int = Field(
        default=0,
        description="已播出但库里没有的正季集数（电影恒 0）——「补齐缺集」的依据",
    )
    added_at: datetime | None = Field(
        default=None, description="最近一次文件入账时间（首页「最近添加」排序依据）"
    )

    @field_serializer("added_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()


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


class MissingFileView(BaseModel):
    """缺失清单里的一个文件。"""

    id: int
    file_path: str
    season_number: int
    episode_number: int
    size_bytes: int


class MissingItemView(BaseModel):
    """缺失清单的一行：按媒体条目聚合（一个条目可能缺多个文件）。"""

    media_item_id: int
    kind: MediaKind
    tmdb_id: int
    title: str
    year: int | None
    poster_url: str | None
    subscription_id: int | None = Field(
        default=None, description="该条目已有订阅时给出——清理记录前提示用户（订阅可能重新下回来）"
    )
    files: list[MissingFileView]


class MissingClearPayload(BaseModel):
    """清理缺失记录（只删台账行，绝不动磁盘）。media_item_id 缺省 = 清整库。"""

    library_id: int
    media_item_id: int | None = None


class RedownloadPayload(BaseModel):
    """重新下载：把某条目的缺失单元交回订阅管线。"""

    library_id: int
    media_item_id: int


class UnidentifiedClearPayload(BaseModel):
    """批量忽略整库的待识别文件（只删台账，绝不动磁盘）。"""

    library_id: int


class ScanResultView(BaseModel):
    """扫描启动响应。"""

    started: bool
    message: str
