from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class DownloaderType(StrEnum):
    """已适配的下载器类型。"""

    QBITTORRENT = "qbittorrent"
    TRANSMISSION = "transmission"


class DownloaderConfig(BaseModel):
    """下载器连接配置。

    url 填下载器 Web 服务的完整地址：
    - qBittorrent: WebUI 地址，如 ``http://192.168.1.10:8080``
    - Transmission: RPC 地址，如 ``http://192.168.1.10:9091``
      （路径缺省时自动补全为 ``/transmission/rpc``）
    """

    type: DownloaderType
    url: str
    username: str | None = None
    password: str | None = None
    timeout: float = 30.0


class DownloadRequest(BaseModel):
    """提交下载任务的输入参数。

    种子来源二选一（必须且只能提供一个）：
    - torrent_bytes: .torrent 文件内容。PT 站点的种子需要带 cookie 才能下载，
      由 tracker 层的 download_torrent() 取回字节后从这里递交，下载器无需
      也无法直接访问站点。
    - magnet: 磁力链接（或下载器可直接访问的 .torrent URL）。
    """

    torrent_bytes: bytes | None = None
    magnet: str | None = None
    # 保存目录。None 表示使用下载器自己的默认目录。
    save_path: str | None = None
    # 分类：qBittorrent 映射为原生 category；Transmission 没有分类概念，
    # 映射为第一个 label。
    category: str | None = None
    # 标签：qBittorrent 映射为 tags；Transmission 追加到 labels。
    tags: list[str] = Field(default_factory=list)
    # 是否以暂停状态添加（只入队不开始下载）。
    paused: bool = False

    @model_validator(mode="after")
    def _validate_source(self) -> DownloadRequest:
        if (self.torrent_bytes is None) == (self.magnet is None):
            raise ValueError("torrent_bytes 和 magnet 必须且只能提供一个")
        return self


class SubmitResult(BaseModel):
    """提交下载任务的输出。

    调用成功即代表任务已被下载器接收；失败通过 Downloader* 异常抛出。
    info_hash 是跨下载器的统一标识，后续查询/管理该任务都以它为键。
    """

    # 种子 v1 infohash（40 位小写十六进制）。由种子内容/磁力链接本地计算得出，
    # 极少数情况下（纯 v2 磁力链接）无法解析时为 None。
    info_hash: str | None
    # 下载器中的任务名称。提交后未能立即回查到时为空字符串。
    name: str = ""
    # 该种子提交前已存在于下载器中（幂等：不视为错误，也不会重复添加）。
    already_exists: bool = False


class DownloaderInfo(BaseModel):
    """连接测试返回的下载器信息。"""

    type: DownloaderType
    version: str
