"""种子标识计算工具。

infohash 是 BT 协议里种子的全局唯一标识（info 字典 bencode 编码的 SHA-1），
qBittorrent / Transmission 内部都以它作为任务主键。在提交前本地算出
infohash，就能做到：

1. 提交去重 —— 先按 hash 查询下载器，已存在则直接返回 already_exists；
2. 统一输出 —— 两个下载器的 SubmitResult.info_hash 口径完全一致，
   上层无需关心适配的是谁。

注意必须对文件中 info 字典的 **原始字节区间** 做 SHA-1，不能解码后重新
编码 —— 个别站点生成的种子 bencode 不规范（字典 key 未排序），重编码会
得到不同的 hash。因此这里的解析器只定位区间、不重建对象。
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

from movieclaw_downloader.exceptions import TorrentParseError

# magnet 的 xt 参数：urn:btih: 后跟 40 位十六进制或 32 位 base32 的 v1 infohash
_BTIH_RE = re.compile(r"^urn:btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})$")


def compute_info_hash(torrent_bytes: bytes) -> str:
    """从 .torrent 文件内容计算 v1 infohash（40 位小写十六进制）。"""
    try:
        end, spans = _parse_dict_spans(torrent_bytes, 0)
    except (ValueError, IndexError) as exc:
        raise TorrentParseError(
            "种子文件内容不是合法的 bencode 格式", details={"error": str(exc)}
        ) from exc
    span = spans.get(b"info")
    if span is None:
        raise TorrentParseError("种子文件缺少 info 字段，不是有效的 .torrent 文件")
    start, stop = span
    return hashlib.sha1(torrent_bytes[start:stop]).hexdigest()


def parse_magnet_info_hash(magnet: str) -> str | None:
    """从磁力链接提取 v1 infohash，统一为 40 位小写十六进制。

    返回 None 表示无法提取（不是 magnet 链接，或是纯 v2 种子只有 btmh）。
    此时提交仍可进行，只是失去去重和统一标识能力。
    """
    if not magnet.startswith("magnet:"):
        return None
    query = parse_qs(urlsplit(magnet).query)
    for xt in query.get("xt", []):
        match = _BTIH_RE.match(xt)
        if not match:
            continue
        raw = match.group(1)
        if len(raw) == 40:
            return raw.lower()
        # 32 位 base32 编码（部分老站点的 magnet 用这种形式）
        return base64.b32decode(raw.upper()).hex()
    return None


# ---------------------------------------------------------------------------
# 最小 bencode 区间解析器
#
# 只做一件事：扫过一个 bencode 值并返回其结束位置；对字典额外记录每个
# 顶层 value 的字节区间。不构建 Python 对象，天然保留原始字节。
# ---------------------------------------------------------------------------


def _skip_value(data: bytes, pos: int) -> int:
    """跳过 pos 处的一个 bencode 值，返回其结束位置（开区间）。"""
    head = data[pos : pos + 1]
    if head == b"i":  # 整数 i<digits>e
        return data.index(b"e", pos) + 1
    if head == b"l":  # 列表 l<values>e
        pos += 1
        while data[pos : pos + 1] != b"e":
            pos = _skip_value(data, pos)
        return pos + 1
    if head == b"d":  # 字典 d<key><value>...e
        end, _ = _parse_dict_spans(data, pos)
        return end
    if head.isdigit():  # 字符串 <len>:<bytes>
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        if length < 0:
            raise ValueError(f"字符串长度非法: {length}")
        end = colon + 1 + length
        if end > len(data):
            raise ValueError("字符串长度超出文件末尾")
        return end
    raise ValueError(f"位置 {pos} 出现未知的 bencode 类型标记: {head!r}")


def _parse_dict_spans(data: bytes, pos: int) -> tuple[int, dict[bytes, tuple[int, int]]]:
    """解析 pos 处的 bencode 字典，返回 (结束位置, {key: value 字节区间})。"""
    if data[pos : pos + 1] != b"d":
        raise ValueError(f"位置 {pos} 不是 bencode 字典")
    pos += 1
    spans: dict[bytes, tuple[int, int]] = {}
    while data[pos : pos + 1] != b"e":
        colon = data.index(b":", pos)
        key_len = int(data[pos:colon])
        key = data[colon + 1 : colon + 1 + key_len]
        value_start = colon + 1 + key_len
        value_end = _skip_value(data, value_start)
        spans[key] = (value_start, value_end)
        pos = value_end
    return pos + 1, spans
