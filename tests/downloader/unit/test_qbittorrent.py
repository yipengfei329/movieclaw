"""QBittorrentDownloader 适配器单元测试。

用假客户端替换 qbittorrent-api 的 Client，不发送真实 HTTP 请求。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import qbittorrentapi

from movieclaw_downloader.clients.qbittorrent import QBittorrentDownloader
from movieclaw_downloader.exceptions import (
    DownloaderAuthError,
    DownloaderConnectError,
    DownloaderSubmitError,
)
from movieclaw_downloader.models import DownloaderConfig, DownloaderType, DownloadRequest
from movieclaw_downloader.torrent import compute_info_hash

TORRENT_BYTES = (
    b"d4:infod6:lengthi1024e4:name8:test.mkv12:piece lengthi16384e6:pieces20:"
    + b"\x01" * 20
    + b"ee"
)
TORRENT_HASH = compute_info_hash(TORRENT_BYTES)

CONFIG = DownloaderConfig(
    type=DownloaderType.QBITTORRENT,
    url="http://localhost:8080",
    username="admin",
    password="pass",
)


class FakeQbtClient:
    """模拟 qbittorrent-api Client：内部维护一个按 hash 索引的种子表。"""

    def __init__(self, add_response: str = "Ok."):
        self.store: dict[str, SimpleNamespace] = {}
        self.add_response = add_response
        self.add_calls: list[dict] = []
        # 添加成功后自动登记到 store 的 (hash, name)，模拟下载器注册行为
        self.register_on_add: tuple[str, str] | None = (TORRENT_HASH, "test.mkv")

    def torrents_info(self, torrent_hashes=None):
        found = self.store.get(torrent_hashes)
        return [found] if found else []

    def torrents_add(self, **kwargs):
        self.add_calls.append(kwargs)
        if self.add_response == "Ok." and self.register_on_add:
            info_hash, name = self.register_on_add
            self.store[info_hash] = SimpleNamespace(hash=info_hash, name=name)
        return self.add_response

    def auth_log_in(self):
        pass

    def app_version(self):
        return "v5.0.2"


def make_downloader(fake: FakeQbtClient) -> QBittorrentDownloader:
    downloader = QBittorrentDownloader(CONFIG)
    downloader._qbt = fake
    return downloader


class TestSubmit:
    async def test_submit_torrent_bytes(self):
        fake = FakeQbtClient()
        downloader = make_downloader(fake)

        result = await downloader.submit(
            DownloadRequest(
                torrent_bytes=TORRENT_BYTES,
                save_path="/downloads/movies",
                category="movies",
                tags=["pt", "auto"],
                paused=True,
            )
        )

        assert result.info_hash == TORRENT_HASH
        assert result.name == "test.mkv"
        assert result.already_exists is False

        call = fake.add_calls[0]
        assert call["torrent_files"] == TORRENT_BYTES
        assert call["urls"] is None
        assert call["save_path"] == "/downloads/movies"
        assert call["category"] == "movies"
        assert call["tags"] == ["pt", "auto"]
        assert call["is_paused"] is True
        assert call["use_auto_torrent_management"] is False

    async def test_submit_magnet(self):
        fake = FakeQbtClient()
        magnet = f"magnet:?xt=urn:btih:{TORRENT_HASH}"
        downloader = make_downloader(fake)

        result = await downloader.submit(DownloadRequest(magnet=magnet))

        assert result.info_hash == TORRENT_HASH
        assert fake.add_calls[0]["urls"] == magnet
        assert fake.add_calls[0]["torrent_files"] is None

    async def test_already_exists_is_idempotent(self):
        fake = FakeQbtClient()
        fake.store[TORRENT_HASH] = SimpleNamespace(hash=TORRENT_HASH, name="existing.mkv")
        downloader = make_downloader(fake)

        result = await downloader.submit(DownloadRequest(torrent_bytes=TORRENT_BYTES))

        assert result.already_exists is True
        assert result.name == "existing.mkv"
        assert fake.add_calls == []  # 未重复提交

    async def test_add_fails_raises(self):
        fake = FakeQbtClient(add_response="Fails.")
        downloader = make_downloader(fake)

        with pytest.raises(DownloaderSubmitError):
            await downloader.submit(DownloadRequest(torrent_bytes=TORRENT_BYTES))

    async def test_connection_error_translated(self):
        fake = FakeQbtClient()

        def raise_conn(**kwargs):
            raise qbittorrentapi.APIConnectionError("boom")

        fake.torrents_add = raise_conn
        downloader = make_downloader(fake)

        with pytest.raises(DownloaderConnectError):
            await downloader.submit(DownloadRequest(torrent_bytes=TORRENT_BYTES))


class TestConnection:
    async def test_test_connection(self):
        downloader = make_downloader(FakeQbtClient())
        info = await downloader.test_connection()
        assert info.type == DownloaderType.QBITTORRENT
        assert info.version == "v5.0.2"

    async def test_login_failed_translated(self):
        fake = FakeQbtClient()

        def raise_login():
            raise qbittorrentapi.LoginFailed("bad credentials")

        fake.auth_log_in = raise_login
        downloader = make_downloader(fake)

        with pytest.raises(DownloaderAuthError):
            await downloader.test_connection()
