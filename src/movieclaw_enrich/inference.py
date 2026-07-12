"""小模型推理层——片名/年份/季集/双轴分类的抽取实现。

模型是 ml/ 目录训练的多任务 ONNX（共享编码器 + token BIO 头 + media/content
两个分类头，基座 chinese-lert-small，int8 量化，CPU 单条约 3ms）。本模块是它
在主项目里的唯一消费点，设计约束：

- **模型只圈 span，数值由确定性代码解析**（"第三季"→3、"E01-E06"→[1..6]），
  绝不让模型直接产出数字；
- **置信度门槛**：span 内 token 的平均置信度低于阈值即丢弃——宁缺毋滥，与
  enrich 层"绝不返回猜测值"的铁律一致；
- **优雅缺席**：模型文件不存在时打一次中文警告后禁用，相关字段保持空值，
  服务照常启动（部署者可稍后补模型文件）。

模型文件目录（默认 ``data/models/torrent-ner``，可用环境变量 MOVIECLAW_NER_DIR
覆盖）需包含：model.int8.onnx、tokenizer.json、labels.json——由
``ml/torrent_ner/export.py`` 一次性产出，发布走 GitHub Release。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path

logger = logging.getLogger("movieclaw_enrich")

# span 平均置信度低于此值即丢弃（softmax 概率）；分类头低于此值输出未知
_MIN_SPAN_PROB = 0.5
_MIN_CLS_PROB = 0.5

# 号码守卫（沿袭原季集提取器的取值边界）：越界视为误命中
_MAX_SEASON = 100
_MAX_EPISODE = 1900
_MAX_EPISODE_SPAN = 500

_CN_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_NUM_RE = re.compile(r"[0-9]{1,4}|[一二两三四五六七八九十]{1,3}")
_RANGE_SEP_RE = re.compile(r"[-~至到]")
_YEAR_RE = re.compile(r"(?<!\d)(18|19|20)\d{2}(?!\d)")
_CJK_RE = re.compile(r"[一-鿿]")

# 数字吸附与区间合并只作用于号码类字段（片名 span 不能动边界）
_NUMERIC_FIELDS = {"YEAR", "SEASON", "EPISODE", "EPISODE_TOTAL"}
# 同字段相邻 span 之间允许桥接的间隙："S01-S05" 被拆成两段时中间是 "-"/"-S"
_RANGE_GAP_RE = re.compile(r"[\s\-~至到]{1,2}[SsEePp]{0,2}")


def _merge_range_spans(spans: list[tuple], texts: tuple[str, str]) -> list[tuple]:
    """把被模型拆开的号码区间重新桥接成单个 span。

    条件：同来源段、同号码类字段、两 span 间隙短且形如区间分隔（"-"、"-S"、
    "至"）。合并后 _parse_numbers 才能看到完整的 "S01-S05" 并展开区间。
    """
    merged: list[list] = []
    for span in sorted(spans, key=lambda s: (s[0], s[2])):
        seq_id, field, start, end, prob = span
        if merged:
            last = merged[-1]
            gap = texts[seq_id][last[3] : start] if last[0] == seq_id else None
            if (
                last[0] == seq_id
                and last[1] == field
                and field in _NUMERIC_FIELDS
                and gap is not None
                and _RANGE_GAP_RE.fullmatch(gap)
            ):
                last[3] = end
                last[4] = min(last[4], prob)
                continue
        merged.append([seq_id, field, start, end, prob])
    return [tuple(s) for s in merged]


def _to_int(text: str) -> int | None:
    """'12' / '十二' / '二十' / '五' → int；无法解析返回 None。"""
    if text.isdigit():
        return int(text)
    if "十" in text:
        tens_part, _, units_part = text.partition("十")
        if tens_part and tens_part not in _CN_DIGITS:
            return None
        if units_part and units_part not in _CN_DIGITS:
            return None
        tens = _CN_DIGITS.get(tens_part, 1) if tens_part else 1
        return tens * 10 + (_CN_DIGITS.get(units_part, 0) if units_part else 0)
    return _CN_DIGITS.get(text)


def _parse_numbers(span_text: str, *, max_value: int, cap: int) -> list[int]:
    """把一个 span 文本解析成号码列表；两个号码夹区间分隔符时展开区间。"""
    matches = list(_NUM_RE.finditer(span_text))
    values = [v for m in matches if (v := _to_int(m.group())) is not None and 0 < v <= max_value]
    if len(matches) == 2 and len(values) == 2:
        between = span_text[matches[0].end() : matches[1].start()]
        if _RANGE_SEP_RE.search(between):
            start, end = values
            if start <= end and end - start + 1 <= cap:
                return list(range(start, end + 1))
            return []
    return values


class _NerModel:
    """ONNX 会话 + tokenizer 的惰性单例（线程安全，加载失败只警告一次）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loaded = False
        self._session = None
        self._tokenizer = None
        self._meta: dict = {}

    def _load(self) -> None:
        model_dir = Path(os.environ.get("MOVIECLAW_NER_DIR", "data/models/torrent-ner"))
        onnx_path = model_dir / "model.int8.onnx"
        if not onnx_path.exists():
            logger.warning(
                "未找到种子名抽取模型（%s），片名/年份/季集字段将保持空值。"
                "请从项目 Release 下载模型文件放入该目录后重启。", onnx_path
            )
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            self._meta = json.loads((model_dir / "labels.json").read_text(encoding="utf-8"))
            self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
            self._tokenizer.enable_truncation(max_length=int(self._meta.get("max_length", 256)))
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # 短序列单线程最快，也避免与事件循环抢核
            self._session = ort.InferenceSession(
                str(onnx_path), options, providers=["CPUExecutionProvider"]
            )
            logger.info("种子名抽取模型已加载：%s", onnx_path)
        except Exception:
            self._session = None
            logger.exception("种子名抽取模型加载失败，相关字段将保持空值")

    def get(self):
        if not self._loaded:
            with self._lock:
                if not self._loaded:
                    self._load()
                    self._loaded = True
        return self._session, self._tokenizer, self._meta


_MODEL = _NerModel()


def _softmax(x, axis: int = -1):
    import numpy as np

    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


def _decode_spans(labels: list[str], probs, ids, offsets, sequence_ids) -> list[tuple]:
    """token 级 BIO 解码成 (来源段, 字段, span文本区间, 平均置信度)。

    实体 = 连续的 B-/I- token 段，字符区间取首 token 起点到末 token 终点——
    不能逐字符涂色再解码：空格不属于任何 token，会把含空格实体拦腰切断。
    I- 接不上前段（非法 BIO）按新实体起点处理（确定性修复）。
    """
    runs: list[list] = []
    current: list | None = None
    for i, seq_id in enumerate(sequence_ids):
        start, end = offsets[i]
        if seq_id is None or start == end:
            current = None
            continue
        tag = labels[ids[i]]
        if tag == "O":
            current = None
            continue
        field = tag[2:]
        prob = float(probs[i, ids[i]])
        if tag.startswith("I-") and current and current[0] == seq_id and current[1] == field:
            current[3] = end
            current[4].append(prob)
        else:
            current = [seq_id, field, start, end, [prob]]
            runs.append(current)
    return [
        (seq_id, field, start, end, sum(p) / len(p))
        for seq_id, field, start, end, p in runs
    ]


def extract_with_model(title: str, subtitle: str = "") -> dict[str, object]:
    """双段联合推理，返回 TorrentAttrs 对应字段的部分字典（模型缺席返回空字典）。"""
    session, tokenizer, meta = _MODEL.get()
    if session is None:
        return {}

    import numpy as np

    enc = tokenizer.encode(title, subtitle or " ")
    inputs = {
        "input_ids": np.array([enc.ids], dtype=np.int64),
        "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
        "token_type_ids": np.array([enc.type_ids], dtype=np.int64),
    }
    span_logits, media_logits, content_logits = session.run(None, inputs)
    span_probs = _softmax(span_logits[0])
    pred_ids = span_probs.argmax(axis=-1)

    texts = (title, subtitle or "")
    spans = [
        s for s in _decode_spans(meta["labels"], span_probs, pred_ids, enc.offsets, enc.sequence_ids)
        if s[4] >= _MIN_SPAN_PROB and s[0] <= 1
    ]
    spans = _merge_range_spans(spans, texts)

    by_field: dict[str, list[str]] = {}
    for seq_id, field, start, end, _prob in spans:
        source = texts[seq_id]
        if field in _NUMERIC_FIELDS:
            # 数字吸附：仅当边界正好切在数字串中间（界内外都是数字）才向外
            # 吸满——模型偶发把 span 从数字中间切开（"第2024期"碎成 '4期'），
            # 吸附后守卫才能看到完整号码。边界在字母/量词上时绝不吸
            # （"S02E05" 的 E05 左边贴着 02，但 E 不是数字，不能吸过去）。
            while start > 0 and source[start].isdigit() and source[start - 1].isdigit():
                start -= 1
            while end < len(source) and source[end - 1].isdigit() and source[end].isdigit():
                end += 1
        if field == "YEAR":
            # 老规则的两条实证守卫，作为模型输出的确定性护栏保留：
            # 紧贴 CJK 的数字是片名一部分（请回答1988）；跟量词的是期号/集号。
            # after 必须非空才做量词判断——空串是任何字符串的子串，不加这个
            # 条件会把位于文本末尾的年份全部误杀（"... | 2026"）
            before = source[start - 1] if start > 0 else ""
            after = source[end] if end < len(source) else ""
            if _CJK_RE.match(before) or (after and after in "期集话話回季"):
                continue
        text = source[start:end].strip()
        if text and text not in by_field.setdefault(field, []):
            by_field[field].append(text)

    result: dict[str, object] = {}
    for field, key in (("TITLE_ZH", "titles_zh"), ("TITLE_EN", "titles_en")):
        titles = by_field.get(field, [])
        if len(titles) > 1:
            # 单字符"别名"几乎必是模型碎片（"金部长"碎成 '金'/'长'）：
            # 已有更长片名时丢弃；真正的单字片名（《影》）通常是唯一主名，保留
            titles = [t for t in titles if len(t) > 1] or titles
        if titles:
            result[key] = titles

    for span_text in by_field.get("YEAR", []):
        m = _YEAR_RE.search(span_text)
        if m:
            result["year"] = int(m.group())
            break

    seasons: list[int] = []
    for span_text in by_field.get("SEASON", []):
        seasons.extend(_parse_numbers(span_text, max_value=_MAX_SEASON, cap=_MAX_SEASON))
    if seasons:
        result["seasons"] = sorted(set(seasons))

    episodes: list[int] = []
    for span_text in by_field.get("EPISODE", []):
        episodes.extend(
            _parse_numbers(span_text, max_value=_MAX_EPISODE, cap=_MAX_EPISODE_SPAN)
        )
    if episodes:
        result["episodes"] = sorted(set(episodes))

    # 总集数：episodes_total 记数值，complete 置真；集号列表缺席时按 1..N 展开
    # （保持旧行为，订阅匹配依赖 episodes 列表判断覆盖范围）
    for span_text in by_field.get("EPISODE_TOTAL", []):
        totals = _parse_numbers(span_text, max_value=_MAX_EPISODE_SPAN, cap=1)
        result["complete"] = True
        if totals:
            result["episodes_total"] = totals[0]
            if not episodes:
                result["episodes"] = list(range(1, totals[0] + 1))
        break

    media_probs = _softmax(media_logits[0])
    if float(media_probs.max()) >= _MIN_CLS_PROB:
        media = meta["media_types"][int(media_probs.argmax())]
        # 语义映射：series→tv 对齐既有枚举；other=非影视，不标注
        result["model_media_type"] = {"movie": "movie", "series": "tv"}.get(media)

    content_probs = _softmax(content_logits[0])
    if float(content_probs.max()) >= _MIN_CLS_PROB:
        content = meta["content_types"][int(content_probs.argmax())]
        if content != "other":  # other 是残差项，不算"观测到特殊题材"
            result["content_type"] = content

    return result
