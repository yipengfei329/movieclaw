"""字符 span 与 BIO 标签之间的转换——训练和评估共用的核心逻辑。

设计要点：标注数据只存**字符级 span**（与分词器无关，换基座模型不用重标），
训练时才通过 fast tokenizer 的 offset_mapping 落到 token 级 BIO。这层转换
是全管线最容易出错的地方，所以独立成模块并配单元测试（tests/test_encoding.py）。
"""

from __future__ import annotations

from torrent_ner.labels import FIELD_PRIORITY, LABEL2ID


def resolve_overlaps(spans: list[dict]) -> list[dict]:
    """贪心消除同一来源字段内的 span 重叠。

    排序规则：更长的 span 优先（"神墓3" 赢过 "神墓"），等长时结构化字段
    优先（YEAR/SEASON/EPISODE 赢过片名）。被压住的 span 直接丢弃。
    """
    ordered = sorted(
        spans,
        key=lambda s: (-(s["end"] - s["start"]), FIELD_PRIORITY.get(s["field"], 99), s["start"]),
    )
    kept: list[dict] = []
    for span in ordered:
        if all(span["end"] <= k["start"] or span["start"] >= k["end"] for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s["start"])


def build_char_tags(text_len: int, spans: list[dict]) -> list[str]:
    """把一组不重叠的字符 span 展开成逐字符的 BIO 标签数组。"""
    tags = ["O"] * text_len
    for span in resolve_overlaps(spans):
        start, end, field = span["start"], span["end"], span["field"]
        if not (0 <= start < end <= text_len):
            raise ValueError(f"span 越界: {span} (文本长度 {text_len})")
        tags[start] = f"B-{field}"
        for i in range(start + 1, end):
            tags[i] = f"I-{field}"
    return tags


def token_label_ids(
    offsets: list[tuple[int, int]],
    sequence_ids: list[int | None],
    char_tags_by_source: tuple[list[str], list[str]],
) -> list[int]:
    """按 token 首字符所在位置的字符标签，给每个 token 赋 BIO label id。

    - 特殊 token（sequence_id 为 None）赋 -100，训练时不计损失；
    - token 起点恰好是实体起点 → B-，落在实体中段 → I-，其余 → O。
      fast tokenizer 的 offset 起点即 token 首个真实字符，直接取用即可。
    """
    ids: list[int] = []
    for (start, _end), seq_id in zip(offsets, sequence_ids):
        if seq_id is None:
            ids.append(-100)
            continue
        tags = char_tags_by_source[seq_id]
        tag = tags[start] if start < len(tags) else "O"
        ids.append(LABEL2ID[tag])
    return ids


def spans_from_char_tags(tags: list[str]) -> list[tuple[str, int, int]]:
    """把逐字符 BIO 标签解码回 (field, start, end) 三元组列表。

    容忍非法序列（I- 前面没有同字段的 B-/I-）：按新实体起点处理，
    这也是线上推理对模型输出做确定性修复的策略。
    """
    spans: list[tuple[str, int, int]] = []
    field, start = None, 0
    for i, tag in enumerate(tags):
        if tag.startswith("B-") or (tag.startswith("I-") and tag[2:] != field):
            if field is not None:
                spans.append((field, start, i))
            field, start = tag[2:], i
        elif tag == "O" and field is not None:
            spans.append((field, start, i))
            field = None
    if field is not None:
        spans.append((field, start, len(tags)))
    return spans
