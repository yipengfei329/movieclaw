"""下载服务层：把种子提交到外部下载软件的统一抽象。

本服务自身不做 BT 下载，只负责"递交"：tracker 层取回种子（或磁力链接）
后，通过这里的统一接口提交给用户自己部署的下载器。

用法：
    from movieclaw_downloader import DownloaderConfig, DownloadRequest, create_downloader

    downloader = create_downloader(DownloaderConfig(type="qbittorrent", url=..., ...))
    result = await downloader.submit(DownloadRequest(torrent_bytes=data, save_path="/downloads"))
    print(result.info_hash, result.already_exists)
"""

from movieclaw_downloader.base import BaseDownloader
from movieclaw_downloader.exceptions import (
    DownloaderAuthError,
    DownloaderConnectError,
    DownloaderException,
    DownloaderNotSupportedError,
    DownloaderSubmitError,
    TorrentParseError,
)
from movieclaw_downloader.factory import create_downloader
from movieclaw_downloader.models import (
    DownloaderConfig,
    DownloaderInfo,
    DownloaderType,
    DownloadRequest,
    SubmitResult,
    TorrentBrief,
    TorrentFile,
    TorrentStatus,
)

__all__ = [
    "BaseDownloader",
    "DownloadRequest",
    "DownloaderAuthError",
    "DownloaderConfig",
    "DownloaderConnectError",
    "DownloaderException",
    "DownloaderInfo",
    "DownloaderNotSupportedError",
    "DownloaderSubmitError",
    "DownloaderType",
    "SubmitResult",
    "TorrentBrief",
    "TorrentFile",
    "TorrentStatus",
    "TorrentParseError",
    "create_downloader",
]
