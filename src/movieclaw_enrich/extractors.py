"""内置提取器集合——**只负责封闭词表的技术字段**。

每个提取器是一个纯函数：``(原始文本) -> 部分字段字典``，提取不到就返回空字典
（绝不返回猜测值）。管线（见 ``__init__.enrich``）逐个调用并合并结果，单个
提取器抛异常只会被跳过，不影响其它字段——与"单站失败不拖垮整次搜索"同一铁律。

分工边界：分辨率/编码/音频/HDR/片源/REMUX/压制组这些**写法固定**的字段走
本模块的词表正则（精确且零成本）；片名/年份/季集/题材这些**边界模糊**的
字段由小模型抽取（见 inference.py）——规则解决写法固定的，模型解决边界
模糊的，两边各守各的。

要新增可提取的信息：写一个新函数，加进 ``EXTRACTORS`` 注册表，并把
``__init__.ENRICH_VERSION`` +1（触发存量数据在下次启动时自动重算）。
"""

from __future__ import annotations

import re

from movieclaw_enrich.vocab import (
    AUDIO_COMPILED,
    DIMENSION_TO_RESOLUTION,
    HDR_COMPILED,
    MEDIA_SOURCE_COMPILED,
    RELEASE_GROUP_CASE,
    RESOLUTION_COMPILED,
    TECH_TOKENS,
    VIDEO_CODEC_COMPILED,
    match_vocab,
)

# -- 技术属性：词表匹配 --------------------------------------------------------

# 尺寸写法兜底：3840x2160 / 1920X1080（裸数字词条会被相邻的 x 挡住，走这里）
_DIMENSION_RE = re.compile(r"(?<![0-9])(\d{3,4})\s?[X×]\s?(\d{3,4})(?![0-9])")


def extract_resolution(text: str) -> dict[str, object]:
    up = text.upper()
    hits = match_vocab(up, RESOLUTION_COMPILED)
    if hits:
        return {"resolution": hits[0]}
    m = _DIMENSION_RE.search(up)
    if m:
        for value in (int(m.group(1)), int(m.group(2))):
            resolution = DIMENSION_TO_RESOLUTION.get(value)
            if resolution:
                return {"resolution": resolution}
    return {}


def extract_video_codec(text: str) -> dict[str, object]:
    hits = match_vocab(text.upper(), VIDEO_CODEC_COMPILED)
    return {"video_codec": hits[0]} if hits else {}


def extract_audio(text: str) -> dict[str, object]:
    hits = match_vocab(text.upper(), AUDIO_COMPILED, multi=True)
    return {"audio": hits} if hits else {}


def extract_hdr(text: str) -> dict[str, object]:
    hits = match_vocab(text.upper(), HDR_COMPILED, multi=True)
    return {"hdr": hits} if hits else {}


def extract_media_source(text: str) -> dict[str, object]:
    hits = match_vocab(text.upper(), MEDIA_SOURCE_COMPILED)
    return {"media_source": hits[0]} if hits else {}


_REMUX_RE = re.compile(r"(?<![A-Za-z])REMUX(?![A-Za-z])")


def extract_remux(text: str) -> dict[str, object]:
    return {"remux": True} if _REMUX_RE.search(text.upper()) else {}


# 全集标记：写法固定（COMPLETE / 全集 / 全话），属封闭词表归本通道；
# 带数字的"全12集"由模型通道抽 EPISODE_TOTAL，二者互补
_COMPLETE_MARKER_RE = re.compile(r"(?<![A-Za-z])COMPLETE(?![A-Za-z])|全[集话話]", re.I)


def extract_complete_marker(text: str) -> dict[str, object]:
    return {"complete": True} if _COMPLETE_MARKER_RE.search(text) else {}


# -- 压制组：尾段优先 ---------------------------------------------------------
# 场景命名惯例：组名在标题末尾的 '-' 之后（"...x265-WiKi"）。策略：
# 1. 先剥掉末尾的括号装饰段（"-CMCT[国语中字]" 的 [国语中字]）；
# 2. 取末尾 '-'/'@' 后的 token 作为候选——含字母、非纯数字、不是技术词
#    （"-REMUX"/"-4K" 结尾不是组名）即采纳，已知组按词表归一大小写；
# 3. 尾段无候选时，按已知组词表找 "-组名" 形态兜底（必须带 '-' 前缀，
#    避免 MovieBot 那种 CHD/TTG 短词命中片名、被迫用位置启发式硬扛的坑）。

_TRAILING_DECOR_RE = re.compile(r"\s*[\[【（(][^\[\]【】（）()]*[\]】）)]\s*$")
_TAIL_GROUP_RE = re.compile(r"[-@]\s?([A-Za-z0-9@!]{2,20})\s*$")

_KNOWN_GROUP_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"-{re.escape(key)}(?![A-Za-z0-9])"), canon)
    for key, canon in sorted(
        RELEASE_GROUP_CASE.items(), key=lambda kv: len(kv[0]), reverse=True
    )
]


def extract_release_group(text: str) -> dict[str, object]:
    stripped = text.rstrip()
    # 最多剥两层末尾装饰（"[中字][DIY]" 这种叠加）
    for _ in range(2):
        cleaned = _TRAILING_DECOR_RE.sub("", stripped)
        if cleaned == stripped:
            break
        stripped = cleaned

    m = _TAIL_GROUP_RE.search(stripped)
    if m:
        token = m.group(1)
        up = token.upper()
        if not token.isdigit() and re.search(r"[A-Za-z]", token) and up not in TECH_TOKENS:
            return {"release_group": RELEASE_GROUP_CASE.get(up, token)}

    up_text = stripped.upper()
    for pattern, canon in _KNOWN_GROUP_PATTERNS:
        if pattern.search(up_text):
            return {"release_group": canon}
    return {}


# -- 注册表 -------------------------------------------------------------------
# 顺序即执行顺序；各提取器彼此独立（不读对方结果），顺序只影响日志可读性。

EXTRACTORS: list[tuple[str, object]] = [
    ("resolution", extract_resolution),
    ("video_codec", extract_video_codec),
    ("audio", extract_audio),
    ("hdr", extract_hdr),
    ("media_source", extract_media_source),
    ("remux", extract_remux),
    ("complete_marker", extract_complete_marker),
    ("release_group", extract_release_group),
]
