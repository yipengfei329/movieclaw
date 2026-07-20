"""ffprobe 介质探测：读文件本体的真实规格（媒体库 L2/L3 共用）。

设计要点（docs/design/library.md 风险①）：
- 依赖系统 ffprobe（随 ffmpeg 安装，Docker 镜像内置）；缺失时**降级为跳过
  探测**，规格列保持 NULL，不阻断入库/扫描——只在首次发现缺失时告警一次；
- 探测是同步子进程调用，调用方须放线程池（asyncio.to_thread）执行；
- 库存画质的真相来自文件本体，不来自种子名（种子名会说谎，文件不会）。
"""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("movieclaw_api.media_probe")

# ffprobe 缺失只告警一次（每次探测都刷屏毫无意义）
_missing_warned = False

# 单文件探测超时：本地文件读元数据通常毫秒级，超时说明文件/存储有问题
_PROBE_TIMEOUT = 30.0


@dataclass(frozen=True)
class MediaSpec:
    """一次探测的结论。字段 None = 该项未能取得（三态铁律）。"""

    resolution: str | None
    video_codec: str | None
    hdr: str | None
    bit_depth: int | None
    duration_seconds: int | None
    bit_rate: int | None


def probe_media(path: str | Path) -> MediaSpec | None:
    """探测单个视频文件；ffprobe 缺失或探测失败返回 None（调用方规格置 NULL）。"""
    global _missing_warned
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
        )
    except FileNotFoundError:
        if not _missing_warned:
            _missing_warned = True
            logger.warning(
                "系统中未找到 ffprobe（随 ffmpeg 安装），介质规格探测已跳过——"
                "入库仍正常进行，规格列将为空。安装 ffmpeg 后新入库的文件会带规格。"
            )
        return None
    except subprocess.TimeoutExpired:
        logger.warning("ffprobe 探测超时（%s 秒）：%s", _PROBE_TIMEOUT, path)
        return None
    if proc.returncode != 0:
        logger.warning(
            "ffprobe 探测失败：%s（%s）", path, proc.stderr.decode(errors="replace")[:200]
        )
        return None
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return _parse_probe(payload)


def _parse_probe(payload: dict) -> MediaSpec:
    video = next(
        (s for s in payload.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    fmt = payload.get("format", {})

    resolution = None
    codec = None
    hdr = None
    bit_depth = None
    if video is not None:
        codec = video.get("codec_name")
        resolution = _resolution_label(video.get("width"), video.get("height"))
        hdr = _hdr_label(video)
        bit_depth = _bit_depth(video)

    return MediaSpec(
        resolution=resolution,
        video_codec=codec,
        hdr=hdr,
        bit_depth=bit_depth,
        duration_seconds=_to_int(fmt.get("duration")),
        bit_rate=_to_int(fmt.get("bit_rate")),
    )


def _resolution_label(width: int | None, height: int | None) -> str | None:
    """宽高 → 行业惯用分辨率标签。以宽度为主判据（电影常有非 16:9 裁切，
    2.39:1 的 4K 片高度只有 ~1600，按高度判会误降档）。"""
    if not width and not height:
        return None
    w = width or 0
    h = height or 0
    if w >= 3200 or h >= 1900:
        return "2160p"
    if w >= 1800 or h >= 1000:
        return "1080p"
    if w >= 1200 or h >= 700:
        return "720p"
    if h:
        return f"{h}p"
    return None


def _hdr_label(video: dict) -> str | None:
    """从传输特性判定 HDR 基础格式；SDR 返回 None。

    Dolby Vision 的可靠判定需要 side_data/配置记录，各容器差异大，
    v1 先覆盖 HDR10（PQ）与 HLG 两大类；DV 层探测留待洗版（P6）细化。
    """
    transfer = (video.get("color_transfer") or "").lower()
    if transfer == "smpte2084":
        return "HDR10"
    if transfer == "arib-std-b67":
        return "HLG"
    return None


def _bit_depth(video: dict) -> int | None:
    raw = _to_int(video.get("bits_per_raw_sample"))
    if raw:
        return raw
    pix_fmt = video.get("pix_fmt") or ""
    if "12le" in pix_fmt or "12be" in pix_fmt:
        return 12
    if "10le" in pix_fmt or "10be" in pix_fmt:
        return 10
    if pix_fmt:
        return 8
    return None


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
