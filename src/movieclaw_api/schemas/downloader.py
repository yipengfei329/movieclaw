from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_serializer, field_validator

from movieclaw_db.models.downloader_client import ClientType, DownloaderClient
from movieclaw_db.models.site_credential import ConfigStatus


class PathMapping(BaseModel):
    """一条路径映射：movieclaw 视角的目录前缀 → 下载器视角的对应前缀。

    跨容器/跨主机部署时同一块盘两边挂载路径不同（movieclaw 看到
    ``/data/downloads``，下载器容器里是 ``/downloads``），提交下载前
    按最长前缀把保存目录翻译成下载器视角。视角一致的部署无需配置。
    """

    local: str = Field(min_length=1, description="movieclaw 上的路径前缀")
    remote: str = Field(min_length=1, description="下载器上的对应路径前缀")

    @field_validator("local", "remote")
    @classmethod
    def _validate_abs(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith("/"):
            raise ValueError("路径映射两端都必须是以 / 开头的绝对路径")
        return value


class DownloaderView(BaseModel):
    """下载器配置的对外视图（**脱敏**：绝不回传密码）。"""

    id: int
    name: str
    client_type: ClientType
    url: str
    username: str | None = None
    save_path: str | None = Field(default=None, description="提交下载时的默认保存目录")
    path_mappings: list[PathMapping] | None = Field(
        default=None, description="路径映射（movieclaw 路径 → 下载器路径）"
    )
    enabled: bool
    is_default: bool = Field(description="是否为默认下载器（一键下载不选目标时投给它）")
    status: ConfigStatus
    usable: bool = Field(description="是否可用 = 已启用且连接测试通过（status=active）")
    version: str | None = Field(default=None, description="最近一次连接成功获取的版本号")
    last_error: str | None = Field(default=None, description="最近测试失败原因（清晰中文）")
    last_checked_at: datetime | None = Field(default=None, description="最近一次测试时间")
    created_at: datetime
    updated_at: datetime

    @field_serializer("last_checked_at", "created_at", "updated_at")
    def _serialize_utc(self, value: datetime | None) -> str | None:
        """库内 naive UTC 补时区标记再输出，理由见 schemas.site.ConfiguredSite。"""
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()

    @classmethod
    def from_model(cls, row: DownloaderClient) -> DownloaderView:
        """从 ORM 记录构造脱敏视图。只挑选可公开字段，天然屏蔽密码密文。"""
        return cls(
            id=row.id,  # type: ignore[arg-type]  # 落库后必有主键
            name=row.name,
            client_type=row.client_type,
            url=row.url,
            username=row.username,
            save_path=row.save_path,
            path_mappings=row.path_mappings,  # type: ignore[arg-type]  # 落库前已按 PathMapping 规范化
            enabled=row.enabled,
            is_default=row.is_default,
            status=row.status,
            usable=row.enabled and row.status == ConfigStatus.ACTIVE,
            version=row.version,
            last_error=row.last_error,
            last_checked_at=row.last_checked_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class DownloaderPayload(BaseModel):
    """新增/更新下载器的请求体（更新时 id 走路径参数）。

    与站点配置同语义：更新是**全字段覆盖**，密码出于安全不回显，
    编辑时需要重新填写（未填则视为该下载器无需密码）。
    """

    name: str = Field(min_length=1, max_length=50, description="下载器名称（全局唯一）")
    client_type: ClientType = Field(description="下载器类型：qbittorrent / transmission")
    url: str = Field(description="下载器地址，如 http://192.168.1.10:8080")
    username: str | None = Field(default=None, description="登录用户名（未开鉴权可留空）")
    password: str | None = Field(default=None, description="登录密码（未开鉴权可留空）")
    save_path: str | None = Field(default=None, description="默认保存目录（留空用下载器默认）")
    path_mappings: list[PathMapping] | None = Field(
        default=None,
        description="路径映射（movieclaw 路径 → 下载器路径，视角一致时留空）",
    )
    enabled: bool = Field(default=True, description="是否启用（默认启用）")

    @field_validator("name", "url", "username", "password", "save_path", mode="before")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        """去除首尾空白；空串归一为 None（可选字段"没填"的统一表达）。"""
        if isinstance(value, str):
            value = value.strip()
            return value or None
        return value

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("下载器地址必须以 http:// 或 https:// 开头")
        return value.rstrip("/")

    @field_validator("path_mappings")
    @classmethod
    def _normalize_mappings(cls, value: list[PathMapping] | None) -> list[PathMapping] | None:
        """空列表归一为 None；两端各自查重。

        同一 movieclaw 路径配两条映射，翻译结果取决于遍历顺序（纯配置错误）；
        两条映射指向同一下载器路径同样是错配。PathMapping 校验已去掉尾部斜杠，
        这里的比较天然把 ``/data/`` 与 ``/data`` 视为重复。
        """
        if not value:
            return None
        locals_ = [m.local for m in value]
        if len(set(locals_)) != len(locals_):
            raise ValueError("路径映射中 movieclaw 路径不能重复")
        remotes = [m.remote for m in value]
        if len(set(remotes)) != len(remotes):
            raise ValueError("路径映射中下载器路径不能重复")
        return value


class DownloaderStatusUpdate(BaseModel):
    """启用/停用请求体。"""

    enabled: bool


class DownloadSubmitPayload(BaseModel):
    """手动提交下载的请求体：搜索结果里的一条种子。

    site_id + download_url 均来自搜索接口返回的 TorrentHit，后端凭它们
    带站点登录态取回 .torrent 字节再递交下载器。
    """

    site_id: str = Field(min_length=1, description="种子所属站点 ID（TorrentHit.site_id）")
    download_url: str = Field(min_length=1, description="种子下载入口（TorrentHit.download_url）")
    # 入库目标（可选）：带 library_id 时保存目录改为由库推导（主根/标题 (年份)）。
    # title/year 来自搜索结果的解析实体；无法确定条目身份时只带 library_id，
    # 落到库主根目录。三者都缺省 = 维持原行为（下载器默认目录）。
    library_id: int | None = Field(default=None, description="入库到哪个媒体库")
    title: str | None = Field(default=None, description="条目标题（推导条目子目录用）")
    year: int | None = Field(default=None, description="条目年份")
    # 副标题作识别信号：落 download_hint，扫描器用其中的中文片名/「全N集」
    # 收敛拼音命名种子（TorrentHit.subtitle 原样带过来即可）
    subtitle: str | None = Field(default=None, description="种子副标题（识别线索用）")
    # 用户在下载弹窗里手选的保存目录（movieclaw 视角）：给出时优先于库推导，
    # 提交前照常过路径映射翻译与覆盖守门
    save_path: str | None = Field(default=None, description="手选保存目录（覆盖库推导）")

    @field_validator("save_path")
    @classmethod
    def _validate_save_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().rstrip("/")
        if not value.startswith("/"):
            raise ValueError("保存目录必须是以 / 开头的绝对路径")
        return value


class DownloadSubmitView(BaseModel):
    """手动提交下载的结果视图。"""

    info_hash: str | None = Field(description="种子 infohash（极少数纯 v2 磁力无法解析时为空）")
    name: str = Field(description="下载器中的任务名称（提交后未能立即回查到时为空）")
    already_exists: bool = Field(description="种子提交前已存在于下载器（幂等，未重复添加）")
    downloader_id: int = Field(description="接收本次提交的下载器 ID")
    downloader_name: str = Field(description="接收本次提交的下载器名称")
    save_path: str | None = Field(
        description="实际使用的保存目录（下载器视角，已过路径映射；空 = 下载器自身默认目录）"
    )
