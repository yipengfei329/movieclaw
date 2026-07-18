from __future__ import annotations

from movieclaw_downloader.base import BaseDownloader
from movieclaw_downloader.clients.qbittorrent import QBittorrentDownloader
from movieclaw_downloader.clients.transmission import TransmissionDownloader
from movieclaw_downloader.exceptions import DownloaderNotSupportedError
from movieclaw_downloader.models import DownloaderConfig, DownloaderType

# 类型 → 适配器实现。新增下载器只需实现 BaseDownloader 并在此登记。
_ADAPTERS: dict[DownloaderType, type[BaseDownloader]] = {
    DownloaderType.QBITTORRENT: QBittorrentDownloader,
    DownloaderType.TRANSMISSION: TransmissionDownloader,
}


def create_downloader(config: DownloaderConfig) -> BaseDownloader:
    """按配置创建下载器适配器实例。上层只依赖 BaseDownloader 接口。"""
    adapter = _ADAPTERS.get(config.type)
    if adapter is None:
        raise DownloaderNotSupportedError(str(config.type))
    return adapter(config)
