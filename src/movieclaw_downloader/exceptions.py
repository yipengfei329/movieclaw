from __future__ import annotations

from typing import Any


class DownloaderException(Exception):
    """所有下载器模块异常的基类。"""

    def __init__(
        self,
        message: str = "downloader operation failed",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class DownloaderConnectError(DownloaderException):
    """无法连接到下载器（地址错误、服务未启动、网络不通等）。"""

    def __init__(
        self,
        message: str = "无法连接到下载器，请检查地址和端口",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class DownloaderAuthError(DownloaderException):
    """下载器认证失败（用户名或密码错误、IP 被封禁等）。"""

    def __init__(
        self,
        message: str = "下载器认证失败，请检查用户名和密码",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class DownloaderSubmitError(DownloaderException):
    """提交下载任务失败（种子文件无效、下载器拒绝等）。"""

    def __init__(
        self,
        message: str = "提交下载任务失败",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class TorrentParseError(DownloaderException):
    """种子文件或磁力链接解析失败（内容不是合法的 bencode / magnet 格式）。"""

    def __init__(
        self,
        message: str = "种子内容解析失败",
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details=details)


class DownloaderNotSupportedError(DownloaderException):
    """请求的下载器类型尚未适配。"""

    def __init__(
        self,
        downloader_type: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"下载器类型 '{downloader_type}' 尚未支持", details=details)
        self.downloader_type = downloader_type
