"""infohash 计算工具（bencode 区间解析 + magnet 解析）单元测试。"""

from __future__ import annotations

import base64
import hashlib

import pytest

from movieclaw_downloader.exceptions import TorrentParseError
from movieclaw_downloader.torrent import compute_info_hash, parse_magnet_info_hash

# 手工构造的最小 info 字典（合法 bencode，key 按规范排序）
INFO_BYTES = (
    b"d6:lengthi1024e4:name8:test.mkv12:piece lengthi16384e6:pieces20:" + b"\x01" * 20 + b"e"
)
INFO_HASH = hashlib.sha1(INFO_BYTES).hexdigest()


class TestComputeInfoHash:
    def test_minimal_torrent(self):
        torrent = b"d8:announce18:http://tracker/ann4:info" + INFO_BYTES + b"e"
        assert compute_info_hash(torrent) == INFO_HASH

    def test_info_not_last_key(self):
        """info 后面还有其他字段（如 comment）时，区间定位仍然正确。"""
        torrent = b"d4:info" + INFO_BYTES + b"7:comment3:abce"
        assert compute_info_hash(torrent) == INFO_HASH

    def test_non_canonical_key_order_preserved(self):
        """info 内部 key 未按规范排序时，必须对原始字节算 hash（不可重编码）。"""
        unsorted_info = (
            b"d4:name8:test.mkv6:lengthi1024e12:piece lengthi16384e6:pieces20:"
            + b"\x02" * 20
            + b"e"
        )
        torrent = b"d4:info" + unsorted_info + b"e"
        assert compute_info_hash(torrent) == hashlib.sha1(unsorted_info).hexdigest()

    def test_nested_structures_skipped(self):
        """info 前有嵌套 list/dict 字段时能正确跳过。"""
        torrent = b"d13:announce-listll18:http://tracker/annee4:info" + INFO_BYTES + b"e"
        assert compute_info_hash(torrent) == INFO_HASH

    def test_invalid_bencode_raises(self):
        with pytest.raises(TorrentParseError):
            compute_info_hash(b"<html>not a torrent</html>")

    def test_truncated_file_raises(self):
        torrent = b"d4:info" + INFO_BYTES + b"e"
        with pytest.raises(TorrentParseError):
            compute_info_hash(torrent[:20])

    def test_missing_info_raises(self):
        with pytest.raises(TorrentParseError, match="info"):
            compute_info_hash(b"d8:announce18:http://tracker/anne")


class TestParseMagnetInfoHash:
    def test_hex_hash_lowercased(self):
        magnet = f"magnet:?xt=urn:btih:{INFO_HASH.upper()}&dn=test"
        assert parse_magnet_info_hash(magnet) == INFO_HASH

    def test_base32_hash_normalized_to_hex(self):
        b32 = base64.b32encode(bytes.fromhex(INFO_HASH)).decode()
        magnet = f"magnet:?xt=urn:btih:{b32}"
        assert parse_magnet_info_hash(magnet) == INFO_HASH

    def test_not_magnet_returns_none(self):
        assert parse_magnet_info_hash("https://example.com/file.torrent") is None

    def test_v2_only_magnet_returns_none(self):
        magnet = "magnet:?xt=urn:btmh:1220" + "a" * 64
        assert parse_magnet_info_hash(magnet) is None
