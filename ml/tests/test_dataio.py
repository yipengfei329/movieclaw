"""dataio.py 的单元测试——切分稳定性与 review 隔离是训练数据完整性的两道闸。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.dataio import load_split, split_of


def test_split_of_stable_and_valid():
    # 同一 id 永远同一切分（增量重训不污染测试集的前提）
    for sample_id in ("chdbits:123", "mteam:abc", "ttg:999"):
        first = split_of(sample_id)
        assert first in ("train", "dev", "test")
        assert all(split_of(sample_id) == first for _ in range(3))


def test_split_ratio_roughly_80_10_10():
    from collections import Counter

    counts = Counter(split_of(f"site:{i}") for i in range(5000))
    assert 0.75 <= counts["train"] / 5000 <= 0.85
    assert 0.06 <= counts["dev"] / 5000 <= 0.14
    assert 0.06 <= counts["test"] / 5000 <= 0.14


def test_load_split_clean_only_quarantines_review(tmp_path):
    # 构造覆盖三种情况的 id：干净、带 review、review 为空列表（人工修正后）
    records = []
    for i in range(60):
        record = {"id": f"s:{i}", "title": "t", "subtitle": "", "spans": []}
        if i % 3 == 1:
            record["review"] = ["title_zh 子串未在原文找到: 'x'"]
        elif i % 3 == 2:
            record["review"] = []  # 空列表 = 已人工处理，应视为干净
        records.append(record)
    path = tmp_path / "labeled.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))

    for split in ("train", "dev", "test"):
        every = load_split(path, split)
        clean = load_split(path, split, clean_only=True)
        # 只有非空 review 被隔离；空列表 review 自动回流
        assert clean == [r for r in every if not r.get("review")]
        assert all(not r.get("review") for r in clean)
    total_clean = sum(len(load_split(path, s, clean_only=True)) for s in ("train", "dev", "test"))
    assert total_clean == 40  # 60 条中 20 条带非空 review
