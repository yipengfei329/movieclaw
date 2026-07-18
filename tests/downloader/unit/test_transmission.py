"""TransmissionDownloader 适配器单元测试。

用假客户端替换 transmission-rpc 的 Client，不发送真实 HTTP 请求。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from transmission_rpc.error import TransmissionAuthError, TransmissionConnectError

from movieclaw_downloader.clients.transmission import TransmissionDownloader
from movieclaw_downloader.exceptions import (
    DownloaderAuthError,
    DownloaderConnectError,
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
    type=DownloaderType.TRANSMISSION,
    url="http://localhost:9091",
    username="admin",
    password="pass",
)


class FakeTrClient:
    """模拟 transmission-rpc Client：按 hash 索引的种子表。"""

    def __init__(self):
        self.store: dict[str, SimpleNamespace] = {}
        self.add_calls: list[tuple] = []

    def get_torrent(self, torrent_id):
        if torrent_id not in self.store:
            raise KeyError("Torrent not found in result")
        return self.store[torrent_id]

    def add_torrent(self, torrent, **kwargs):
        self.add_calls.append((torrent, kwargs))
        return SimpleNamespace(name="test.mkv", hash_string=TORRENT_HASH)

    def get_session(self):
        return SimpleNamespace(version="4.0.5")


def make_downloader(fake: FakeTrClient) -> TransmissionDownloader:
    downloader = TransmissionDownloader(CONFIG)
    downloader._tr = fake
    return downloader


class TestSubmit:
    async def test_submit_torrent_bytes(self):
        fake = FakeTrClient()
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

        torrent, kwargs = fake.add_calls[0]
        assert torrent == TORRENT_BYTES
        assert kwargs["download_dir"] == "/downloads/movies"
        # category 映射为第一个 label，tags 追加其后
        assert kwargs["labels"] == ["movies", "pt", "auto"]
        assert kwargs["paused"] is True

    async def test_submit_magnet_without_labels(self):
        fake = FakeTrClient()
        magnet = f"magnet:?xt=urn:btih:{TORRENT_HASH}"
        downloader = make_downloader(fake)

        result = await downloader.submit(DownloadRequest(magnet=magnet))

        assert result.info_hash == TORRENT_HASH
        torrent, kwargs = fake.add_calls[0]
        assert torrent == magnet
        assert kwargs["labels"] is None  # 无 category/tags 时不触发 labels 的版本要求

    async def test_v2_magnet_falls_back_to_client_hash(self):
        """本地解析不出 hash 的磁力链接，退用 Transmission 返回的 hash。"""
        fake = FakeTrClient()
        downloader = make_downloader(fake)

        result = await downloader.submit(
            DownloadRequest(magnet="magnet:?xt=urn:btmh:1220" + "a" * 64)
        )

        assert result.info_hash == TORRENT_HASH  # 来自 add_torrent 的返回值

    async def test_already_exists_is_idempotent(self):
        fake = FakeTrClient()
        fake.store[TORRENT_HASH] = SimpleNamespace(name="existing.mkv", hash_string=TORRENT_HASH)
        downloader = make_downloader(fake)

        result = await downloader.submit(DownloadRequest(torrent_bytes=TORRENT_BYTES))

        assert result.already_exists is True
        assert result.name == "existing.mkv"
        assert fake.add_calls == []  # 未重复提交

    async def test_auth_error_translated(self):
        fake = FakeTrClient()

        def raise_auth(torrent, **kwargs):
            raise TransmissionAuthError("401")

        fake.add_torrent = raise_auth
        downloader = make_downloader(fake)

        with pytest.raises(DownloaderAuthError):
            await downloader.submit(DownloadRequest(torrent_bytes=TORRENT_BYTES))

    async def test_connect_error_translated(self):
        fake = FakeTrClient()

        def raise_conn(torrent, **kwargs):
            raise TransmissionConnectError("refused")

        fake.add_torrent = raise_conn
        downloader = make_downloader(fake)

        with pytest.raises(DownloaderConnectError):
            await downloader.submit(DownloadRequest(torrent_bytes=TORRENT_BYTES))


class TestConnection:
    async def test_test_connection(self):
        downloader = make_downloader(FakeTrClient())
        info = await downloader.test_connection()
        assert info.type == DownloaderType.TRANSMISSION
        assert info.version == "4.0.5"

    def test_invalid_url_rejected(self):
        downloader = TransmissionDownloader(
            DownloaderConfig(type=DownloaderType.TRANSMISSION, url="not-a-url")
        )
        with pytest.raises(DownloaderConnectError):
            downloader._client()
