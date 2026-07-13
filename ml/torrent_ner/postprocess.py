"""解码期结构后处理：用副标题的分隔符结构约束模型输出。

数据依据（18.6k 金标实测）：片名 span 跨越 /|丨 分隔符的比例仅 0.1%——
分隔符是近乎绝对的边界。三条确定性规则：

R1 切割：span 跨越分隔符 → 在边界处切断，保留含首字符的那段；
R2 吸附：span 覆盖某分段 ≥60% 且分段内无技术/标签词 → 扩展到整段
    （治"鬼灭之刃 剧场"截断），再剥尾部 (港)(台) 类地区标记；
R3 滤噪：span 文本命中发布标签词表（音轨/字幕/配音/珍藏版…）→ 丢弃
    （治 "[中日多音轨/...]" 前置括号块误抽）。

模型做填空题，结构规则做选择题——与"确定性逻辑归代码"的分工铁律一致。
"""

from __future__ import annotations

import re

SEPARATOR = re.compile(r"\s*[/|丨｜]\s*")
REGION_SUFFIX = re.compile(r"[（(][港台日美]{1,2}[)）]$")
VERSION_SUFFIX = re.compile(r"\s*(IMAX版|加长版|加長版|导演剪辑版?|未删减版?|修复版|修復版|重制版|重製版|3D版|完整版|典藏版|终极版|終極版)$")
# 发布标签词：整个 span 就是这些内容时必为噪音（不含可能是片名实词的字）
TAG_SPAN = re.compile(
    r"^[\[【（(]?[\s]*(?:中日|中英|国粤|国语|粤语|日语)?(?:多?音[轨軌]|字幕|配音|双语|雙語|"
    r"珍藏版|收藏版|典藏版|简繁|簡繁|内封|内嵌|外挂)+[\s]*[\]】）)]?$"
)
# 分段内含这些词则不做 R2 吸附（分段本身混有发布信息）
NO_SNAP = re.compile(r"音[轨軌]|字幕|配音|简繁|簡繁|内封|内嵌|外挂|WEB|BluRay|1080|2160|x26[45]|HEVC", re.I)
# 段首发布标签词（吸附/对齐前先从段首剥掉）
LEADING_TAGS = re.compile(
    r"^(?:(?:官方|国语|國語|中字|限转|限轉|禁转|禁轉|独占|獨佔|特效|应求|應求|首发|首發|"
    r"DIY|粤语|粵語|双语|雙語|杜比|高清|中日|多音[轨軌])\s+)+"
)
_SNAP_MAX_SEG = 30  # 片名极少超 30 字符；超长分段（合集描述等）不做扩展


def _bracket_zones(text: str) -> list[tuple[int, int]]:
    """[]【】（）() 括号区，分隔符切分时跳过其内部（"[中日多音轨/中文字幕]"）。"""
    zones, stack = [], []
    pairs = {"]": "[", "】": "【", ")": "(", "）": "（"}
    for i, ch in enumerate(text):
        if ch in "[【(（":
            stack.append(i)
        elif ch in pairs and stack:
            zones.append((stack.pop(), i + 1))
    return zones


def segments(text: str) -> list[tuple[int, int]]:
    """按分隔符切成 [start, end) 区间；括号内的分隔符不算边界。"""
    zones = _bracket_zones(text)
    bounds, pos = [], 0
    for m in SEPARATOR.finditer(text):
        if any(z[0] <= m.start() < z[1] for z in zones):
            continue
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(text)))
    return [(s, e) for s, e in bounds if e > s]


def refine_title_spans(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """对同一来源文本上的片名 span 列表施加 R1-R3，返回精修后的区间。"""
    segs = segments(text)
    out: list[tuple[int, int]] = []
    for start, end in spans:
        # R1: 跨界切割——保留 span 起点所在的分段部分
        seg = next(((s, e) for s, e in segs if s <= start < e), None)
        if seg is None:
            continue
        if end > seg[1]:
            end = seg[1]
        # R2: 段首标签词剥离（只收缩不扩展——全测试集 A/B 证明扩展类规则
        # 会把"第三季"等段尾成分粘回片名，TITLE_ZH F1 0.908→0.735，已废弃）
        seg_text = text[seg[0]:seg[1]]
        lead = LEADING_TAGS.match(seg_text)
        content_start = seg[0] + (lead.end() if lead else 0)
        start = max(start, content_start)
        # 修剪顺序敏感：先剥完整的尾部地区标记 "(港)"，再剪空白/装饰
        # （右括号不能进通用修剪集，否则 "(港)" 被剥成 "(港" 后无法识别）
        m = REGION_SUFFIX.search(text[start:end])
        if m:
            end = start + m.start()
        m = VERSION_SUFFIX.search(text[start:end])
        if m and m.start() > 0:  # 整个 span 就是版本词时交给 R3 滤掉
            end = start + m.start()
        while start < end and text[start] in " \t*[【「」]":
            start += 1
        while end > start and text[end - 1] in " \t*。．.「」":
            end -= 1
        # R3: 标签词滤噪 + 单字碎片过滤（单字片名极罕见，碎片极常见）
        if end - start <= 1 or TAG_SPAN.match(text[start:end]):
            continue
        out.append((start, end))
    # 去重 + 区间包含消解（仅区间：字符串包含会误杀"千香"⊂"千香引"类合法短别名）
    uniq = sorted(set(out), key=lambda p: p[1] - p[0], reverse=True)
    kept: list[tuple[int, int]] = []
    for s, e in uniq:
        if any(s >= ks and e <= ke for ks, ke in kept):
            continue
        kept.append((s, e))
    return sorted(kept)
