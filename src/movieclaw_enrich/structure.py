"""副标题的分隔符结构知识：片名 span 精修 + 候选别名生成。

数据依据（ml/ 侧 18.6k 金标实测）：片名 span 跨越 /|丨 分隔符的比例仅 0.1%
——分隔符是近乎绝对的片名边界。据此提供两个纯函数：

- ``refine_title_spans``  对模型输出的片名区间做保守精修：跨界切割、段首
  发布标签剥离、尾部句号/版本词/地区标记修剪、标签词滤噪、单字碎片过滤。
  **只收缩不扩展**——扩展类规则已被全测试集 A/B 实证否决（会把"第三季"类
  段尾成分粘回片名，详见 ml/torrent_ner/postprocess.py 留档）。
- ``title_candidates``    把副标题里"像片名但模型没抽出"的分段收为候选别名：
  模型漏抽/字段混淆的保险层。候选只作为 TMDB 匹配的降级查询词，搜不到自然
  淘汰、误报成本≈0，永不直接当作片名展示。
"""

from __future__ import annotations

import re

SEPARATOR = re.compile(r"\s*[/|丨｜]\s*")
_REGION_SUFFIX = re.compile(r"[（(][港台日美]{1,2}[)）]$")
_VERSION_SUFFIX = re.compile(
    r"\s*(IMAX版|加长版|加長版|导演剪辑版?|未删减版?|修复版|修復版|重制版|重製版|3D版|完整版|典藏版|终极版|終極版)$"
)
# 整个 span 就是发布标签内容时必为噪音
_TAG_SPAN = re.compile(
    r"^[\[【（(]?\s*(?:中日|中英|国粤|国语|粤语|日语)?(?:多?音[轨軌]|字幕|配音|双语|雙語|"
    r"珍藏版|收藏版|典藏版|简繁|簡繁|内封|内嵌|外挂)+\s*[\]】）)]?$"
)
# 段首发布标签词（精修时从 span 头剥掉；候选生成时同样剥掉再判断）
_LEADING_TAGS = re.compile(
    r"^(?:(?:官方|国语|國語|中字|限转|限轉|禁转|禁轉|独占|獨佔|特效|应求|應求|首发|首發|"
    r"DIY|粤语|粵語|双语|雙語|杜比|高清|中日|多音[轨軌])\s+)+"
)
# 候选别名的否决词：分段含这些内容说明是发布信息，不是片名
_NOT_TITLE = re.compile(
    r"音[轨軌]|字幕|配音|简繁|簡繁|内封|内嵌|外挂|导演[:：]|主演[:：]|演员[:：]|类型[:：]|"
    r"类别[:：]|WEB|BluRay|BDRip|REMUX|HDR|1080|2160|720[pi]|[xh]26[45]|HEVC|AVC|DTS|"
    r"TrueHD|Atmos|AAC|FLAC|第\s?\d+\s?[集话話期]|全\s?\d+\s?[集话話]|S\d+|转自|轉自|感谢|感謝|"
    r"章节|[国粤台日英]{2,}\S*[语語]|多国语|SUP|菜单|菜單",
    re.I,
)
# 纯题材词分段（"类型:科幻/动画" 被斜杠切开后的尾段）：封闭词表，整段命中即否决
_PURE_GENRE = re.compile(
    r"^(?:剧情|爱情|科幻|动画|動畫|悬疑|懸疑|惊悚|驚悚|犯罪|喜剧|喜劇|动作|動作|奇幻|冒险|冒險|"
    r"战争|戰爭|历史|歷史|传记|傳記|运动|運動|家庭|音乐|音樂|古装|古裝|武侠|武俠|恐怖|纪录|紀錄){1,3}$"
)
_HAS_WORD = re.compile(r"[一-鿿]{2,}|[A-Za-z]{2,}")
_MAX_CANDIDATE_LEN = 30


def _segments(text: str) -> list[tuple[int, int]]:
    """按分隔符切成 [start, end) 区间；括号内的分隔符不算边界。"""
    zones, stack = [], []
    pairs = {"]": "[", "】": "【", ")": "(", "）": "（"}
    for i, ch in enumerate(text):
        if ch in "[【(（":
            stack.append(i)
        elif ch in pairs and stack:
            zones.append((stack.pop(), i + 1))
    bounds, pos = [], 0
    for m in SEPARATOR.finditer(text):
        if any(z[0] <= m.start() < z[1] for z in zones):
            continue
        bounds.append((pos, m.start()))
        pos = m.end()
    bounds.append((pos, len(text)))
    return [(s, e) for s, e in bounds if e > s]


def refine_title_spans(text: str, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """片名区间的保守精修（只收缩不扩展），返回精修后的区间列表。"""
    segs = _segments(text)
    out: list[tuple[int, int]] = []
    for start, end in spans:
        seg = next(((s, e) for s, e in segs if s <= start < e), None)
        if seg is None:
            continue
        # 跨界切割：保留 span 起点所在分段的部分
        end = min(end, seg[1])
        # 段首发布标签剥离
        lead = _LEADING_TAGS.match(text[seg[0]:seg[1]])
        if lead:
            start = max(start, seg[0] + lead.end())
        # 修剪：先剥完整尾部标记（地区/版本），再剪空白与尾部句号
        m = _REGION_SUFFIX.search(text[start:end])
        if m:
            end = start + m.start()
        m = _VERSION_SUFFIX.search(text[start:end])
        if m and m.start() > 0:
            end = start + m.start()
        while start < end and text[start] in " \t*[【「":
            start += 1
        while end > start and text[end - 1] in " \t*。．.「」":
            end -= 1
        # 滤噪：标签词 span、单字碎片
        if end - start <= 1 or _TAG_SPAN.match(text[start:end]):
            continue
        out.append((start, end))
    # 去重 + 区间包含消解（仅区间：字符串包含会误杀"千香"⊂"千香引"类合法短别名）
    uniq = sorted(set(out), key=lambda p: p[1] - p[0], reverse=True)
    kept: list[tuple[int, int]] = []
    for s, e in uniq:
        if not any(s >= ks and e <= ke for ks, ke in kept):
            kept.append((s, e))
    return sorted(kept)


def title_candidates(subtitle: str, known_titles: list[str]) -> list[str]:
    """从副标题分段里收集"像片名但未被抽出"的候选别名（按出现顺序）。

    过滤链：剥段首标签 → 长度 ≤30 → 含实词 → 不含发布信息词 → 不是已知
    片名的子串。注意只做单向判定：**已知名是候选的子串时必须保留候选**——
    这正是模型截断时的恢复通道（已知"鬼灭之刃 剧场" ⊂ 候选"鬼灭之刃 剧场版
    无限列车篇"）。
    """
    out: list[str] = []
    for s, e in _segments(subtitle):
        seg = subtitle[s:e]
        lead = _LEADING_TAGS.match(seg)
        if lead:
            seg = seg[lead.end():]
        # 顺序敏感：先剥完整的尾部地区/版本标记（右括号是标记的一部分，
        # 不能先进通用剥边），再清理两端装饰
        seg = seg.strip(" \t*")
        m = _REGION_SUFFIX.search(seg)
        if m:
            seg = seg[: m.start()]
        m = _VERSION_SUFFIX.search(seg)
        if m and m.start() > 0:
            seg = seg[: m.start()]
        seg = seg.strip(" \t*").lstrip("[【（(「").rstrip("]】）)」").rstrip(" 。．.")
        if not seg or len(seg) > _MAX_CANDIDATE_LEN:
            continue
        if not _HAS_WORD.search(seg) or _NOT_TITLE.search(seg) or _PURE_GENRE.match(seg):
            continue
        if any(seg in known for known in known_titles):
            continue
        if seg not in out:
            out.append(seg)
    return out
