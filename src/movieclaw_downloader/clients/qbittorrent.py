"""qBittorrent 适配器。

基于官方推荐的 qbittorrent-api 包（同步 requests 实现）访问 WebUI API，
所有阻塞调用通过 asyncio.to_thread 放入线程池执行。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import qbittorrentapi

from movieclaw_downloader.base import BaseDownloader
from movieclaw_downloader.exceptions import (
    DownloaderAuthError,
    DownloaderConnectError,
    DownloaderSubmitError,
)
from movieclaw_downloader.models import (
    DownloaderInfo,
    DownloaderType,
    DownloadRequest,
    SubmitResult,
    TorrentBrief,
    TorrentFile,
    TorrentStatus,
)

logger = logging.getLogger("movieclaw_downloader.qbittorrent")

# 提交后回查任务名称的重试节奏：qBittorrent 注册种子是异步的，
# add 返回成功到 torrents_info 可查之间有极短的窗口期。
_LOOKUP_ATTEMPTS = 5
_LOOKUP_INTERVAL = 0.3


@contextmanager
def _translate_errors(url: str) -> Iterator[None]:
    """把 qbittorrent-api 的异常翻译成本模块的统一异常。"""
    try:
        yield
    except qbittorrentapi.LoginFailed as exc:
        raise DownloaderAuthError(
            "qBittorrent 登录失败：用户名或密码错误", details={"url": url}
        ) from exc
    except (qbittorrentapi.Unauthorized401Error, qbittorrentapi.Forbidden403Error) as exc:
        raise DownloaderAuthError(
            "qBittorrent 拒绝访问：请检查凭证，多次失败可能触发了 IP 封禁",
            details={"url": url},
        ) from exc
    except qbittorrentapi.UnsupportedMediaType415Error as exc:
        raise DownloaderSubmitError(
            "qBittorrent 无法识别提交的种子文件（文件损坏或不是 .torrent 格式）",
            details={"url": url},
        ) from exc
    except qbittorrentapi.APIConnectionError as exc:
        raise DownloaderConnectError(
            "无法连接到 qBittorrent，请检查 WebUI 地址和端口",
            details={"url": url, "error": str(exc)},
        ) from exc


class QBittorrentDownloader(BaseDownloader):
    """qBittorrent 下载器适配器。"""

    _qbt: qbittorrentapi.Client | None = None

    def _client(self) -> qbittorrentapi.Client:
        """惰性创建底层客户端（构造无 IO；登录由库在首次请求时自动完成）。"""
        if self._qbt is None:
            self._qbt = qbittorrentapi.Client(
                host=self.config.url,
                username=self.config.username,
                password=self.config.password,
                REQUESTS_ARGS={"timeout": self.config.timeout},
            )
        return self._qbt

    async def submit(self, request: DownloadRequest) -> SubmitResult:
        return await asyncio.to_thread(self._submit_sync, request)

    def _submit_sync(self, request: DownloadRequest) -> SubmitResult:
        client = self._client()
        info_hash = self._resolve_info_hash(request)

        with _translate_errors(self.config.url):
            # 提交前按 infohash 去重：已存在直接幂等返回，不重复添加
            if info_hash:
                existing = client.torrents_info(torrent_hashes=info_hash)
                if existing:
                    logger.info("种子已存在于 qBittorrent，跳过提交: %s", info_hash)
                    return SubmitResult(
                        info_hash=info_hash,
                        name=existing[0].name,
                        already_exists=True,
                    )

            result = client.torrents_add(
                torrent_files=request.torrent_bytes,
                urls=request.magnet,
                save_path=request.save_path,
                category=request.category,
                tags=request.tags or None,
                is_paused=request.paused,
                # 显式关闭自动管理：保证 save_path 生效，不被分类目录规则覆盖
                use_auto_torrent_management=False,
            )
            # 旧版 API 返回 "Ok."/"Fails." 字符串；v2.14+ 返回 metadata 字典
            if isinstance(result, str) and not result.startswith("Ok"):
                raise DownloaderSubmitError(
                    "qBittorrent 拒绝了该种子（文件无效或触发下载器自身的限制）",
                    details={"url": self.config.url, "response": result},
                )

            name = self._lookup_name(client, info_hash)

        logger.info("已提交种子到 qBittorrent: hash=%s name=%s", info_hash, name)
        return SubmitResult(info_hash=info_hash, name=name)

    @staticmethod
    def _lookup_name(client: qbittorrentapi.Client, info_hash: str | None) -> str:
        """提交后回查任务名称；短暂等待注册完成，查不到不视为失败。"""
        if not info_hash:
            return ""
        for attempt in range(_LOOKUP_ATTEMPTS):
            infos = client.torrents_info(torrent_hashes=info_hash)
            if infos:
                return infos[0].name
            if attempt < _LOOKUP_ATTEMPTS - 1:
                time.sleep(_LOOKUP_INTERVAL)
        logger.warning("提交成功但暂未在 qBittorrent 中查到种子: %s", info_hash)
        return ""

    async def get_torrent(self, info_hash: str) -> TorrentStatus | None:
        return await asyncio.to_thread(self._get_torrent_sync, info_hash)

    def _get_torrent_sync(self, info_hash: str) -> TorrentStatus | None:
        client = self._client()
        with _translate_errors(self.config.url):
            infos = client.torrents_info(torrent_hashes=info_hash)
            if not infos:
                return None
            torrent = infos[0]
            files = client.torrents_files(torrent_hash=info_hash)
        return TorrentStatus(
            info_hash=info_hash,
            name=torrent.name,
            progress=float(torrent.progress),
            # progress==1 即全部数据落盘（此后进入做种/完成态）
            completed=float(torrent.progress) >= 1.0,
            save_path=torrent.save_path,
            files=[
                # f.name 是种子内相对路径（含子目录）
                TorrentFile(path=f.name, size_bytes=int(f.size))
                for f in files
            ],
        )

    async def list_torrents(self) -> list[TorrentBrief]:
        return await asyncio.to_thread(self._list_torrents_sync)

    def _list_torrents_sync(self) -> list[TorrentBrief]:
        client = self._client()
        with _translate_errors(self.config.url):
            infos = client.torrents_info()
        briefs = []
        for torrent in infos:
            # content_path 是下载器视角的落盘根路径，末段即磁盘上的真实
            # 目录/文件名（种子在客户端里被改名后 name 会失真，末段不会）
            content = str(getattr(torrent, "content_path", "") or "").rstrip("/\\")
            content_name = content.replace("\\", "/").rsplit("/", 1)[-1] if content else ""
            briefs.append(
                TorrentBrief(
                    name=torrent.name,
                    content_name=content_name or torrent.name,
                    completed=float(torrent.progress) >= 1.0,
                )
            )
        return briefs

    async def test_connection(self) -> DownloaderInfo:
        return await asyncio.to_thread(self._test_connection_sync)

    def _test_connection_sync(self) -> DownloaderInfo:
        client = self._client()
        with _translate_errors(self.config.url):
            client.auth_log_in()
            version = client.app_version()
        return DownloaderInfo(type=DownloaderType.QBITTORRENT, version=version)

    async def close(self) -> None:
        client = self._qbt
        if client is not None:
            # 登出仅是礼貌行为，失败（如连接早已断开）不影响关闭
            with contextlib.suppress(qbittorrentapi.exceptions.APIError):
                await asyncio.to_thread(client.auth_log_out)
            self._qbt = None
