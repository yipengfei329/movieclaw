"""encoding.py 的单元测试——span/BIO 转换是全管线最易错的环节，必须锁死。

    python -m pytest ml/tests/ -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.encoding import (
    build_char_tags,
    resolve_overlaps,
    spans_from_char_tags,
    token_label_ids,
)
from torrent_ner.labels import LABEL2ID


def test_build_char_tags_basic():
    # "神墓 第三季" —— 片名 [0,2) + 季 [3,6)
    spans = [
        {"source": "subtitle", "field": "TITLE_ZH", "start": 0, "end": 2},
        {"source": "subtitle", "field": "SEASON", "start": 3, "end": 6},
    ]
    tags = build_char_tags(6, spans)
    assert tags == ["B-TITLE_ZH", "I-TITLE_ZH", "O", "B-SEASON", "I-SEASON", "I-SEASON"]


def test_resolve_overlaps_longer_wins():
    # "神墓3" 应压过 "神墓"（更长优先）
    spans = [
        {"source": "subtitle", "field": "TITLE_ZH", "start": 0, "end": 2},
        {"source": "subtitle", "field": "TITLE_ZH", "start": 0, "end": 3},
    ]
    kept = resolve_overlaps(spans)
    assert kept == [{"source": "subtitle", "field": "TITLE_ZH", "start": 0, "end": 3}]


def test_resolve_overlaps_priority_on_equal_length():
    # 等长冲突时结构化字段（YEAR）赢过片名
    spans = [
        {"source": "title", "field": "TITLE_EN", "start": 5, "end": 9},
        {"source": "title", "field": "YEAR", "start": 5, "end": 9},
    ]
    kept = resolve_overlaps(spans)
    assert kept[0]["field"] == "YEAR" and len(kept) == 1


def test_spans_roundtrip():
    spans = [
        {"source": "title", "field": "TITLE_EN", "start": 0, "end": 13},
        {"source": "title", "field": "YEAR", "start": 14, "end": 18},
    ]
    tags = build_char_tags(20, spans)
    assert spans_from_char_tags(tags) == [("TITLE_EN", 0, 13), ("YEAR", 14, 18)]


def test_spans_from_char_tags_repairs_illegal_bio():
    # 缺 B- 开头的非法序列按新实体起点修复
    tags = ["O", "I-YEAR", "I-YEAR", "O"]
    assert spans_from_char_tags(tags) == [("YEAR", 1, 3)]


def test_token_label_ids_alignment():
    # 模拟 tokenizer: [CLS] tok(0,4) tok(4,8) [SEP] tok(0,2) [SEP]
    # 第 0 段实体 [0,8) = TITLE_EN；第 1 段实体 [0,2) = TITLE_ZH
    offsets = [(0, 0), (0, 4), (4, 8), (0, 0), (0, 2), (0, 0)]
    sequence_ids = [None, 0, 0, None, 1, None]
    char_tags = (
        build_char_tags(8, [{"field": "TITLE_EN", "start": 0, "end": 8}]),
        build_char_tags(2, [{"field": "TITLE_ZH", "start": 0, "end": 2}]),
    )
    ids = token_label_ids(offsets, sequence_ids, char_tags)
    assert ids == [
        -100,
        LABEL2ID["B-TITLE_EN"],
        LABEL2ID["I-TITLE_EN"],
        -100,
        LABEL2ID["B-TITLE_ZH"],
        -100,
    ]
