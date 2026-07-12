"""守卫审计：量化线上推理层的确定性护栏对模型输出的影响。

对测试集逐条跑两遍 YEAR 抽取（守卫开 / 关），与金标对照，输出四象限：
- guard_saved          守卫拦掉了模型的错值（护栏的存在价值）
- guard_killed_correct 守卫误杀了正确值（**必须为 0，否则护栏有 bug**）
- both_right / both_wrong 守卫未介入的部分

**每次模型重训后必跑**：若某守卫的 saved 长期为 0 且对应的退化用例模型已
自行通过，该守卫应当退役——护栏的存废由本审计裁决，不靠直觉。

    .venv/bin/python ml/torrent_ner/guard_audit.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import numpy as np

from movieclaw_enrich.inference import (
    _CJK_RE,
    _MIN_SPAN_PROB,
    _MODEL,
    _YEAR_RE,
    _decode_spans,
    _normalize_spans,
    _softmax,
)
from torrent_ner.dataio import load_split


def predict_year(session, tok, meta, title: str, subtitle: str, guard: bool) -> int | None:
    """复刻线上 YEAR 抽取路径，guard=False 时跳过否决守卫（吸附/桥接保留）。"""
    enc = tok.encode(title, subtitle or " ")
    inputs = {
        "input_ids": np.array([enc.ids], dtype=np.int64),
        "attention_mask": np.array([enc.attention_mask], dtype=np.int64),
        "token_type_ids": np.array([enc.type_ids], dtype=np.int64),
    }
    span_logits, _, _ = session.run(None, inputs)
    probs = _softmax(span_logits[0])
    ids = probs.argmax(axis=-1)
    texts = (title, subtitle or "")
    spans = [
        s for s in _decode_spans(meta["labels"], probs, ids, enc.offsets, enc.sequence_ids)
        if s[4] >= _MIN_SPAN_PROB and s[0] <= 1
    ]
    for seq, field, start, end, _p in _normalize_spans(spans, texts):
        if field != "YEAR":
            continue
        source = texts[seq]
        if guard:
            before = source[start - 1] if start > 0 else ""
            after = source[end] if end < len(source) else ""
            if _CJK_RE.match(before) or (after and after in "期集话話回季"):
                continue
        m = _YEAR_RE.search(source[start:end])
        if m:
            return int(m.group())
    return None


def gold_year(item: dict) -> int | None:
    texts = {"title": item["title"], "subtitle": item.get("subtitle", "")}
    for s in item["spans"]:
        if s["field"] == "YEAR":
            m = _YEAR_RE.search(texts[s["source"]][s["start"] : s["end"]])
            if m:
                return int(m.group())
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="YEAR 守卫审计")
    parser.add_argument("--data", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--split", default="test")
    args = parser.parse_args()

    session, tok, meta = _MODEL.get()
    if session is None:
        sys.exit("模型未加载（检查 data/models/torrent-ner 或 MOVIECLAW_NER_DIR）")

    items = load_split(args.data, args.split, clean_only=True)
    stats = {"both_right": 0, "both_wrong": 0, "guard_saved": 0, "guard_killed_correct": 0}
    killed: list[str] = []
    for item in items:
        gold = gold_year(item)
        subtitle = item.get("subtitle", "")
        guarded = predict_year(session, tok, meta, item["title"], subtitle, guard=True)
        raw = predict_year(session, tok, meta, item["title"], subtitle, guard=False)
        if guarded == raw:
            stats["both_right" if guarded == gold else "both_wrong"] += 1
        elif guarded == gold:
            stats["guard_saved"] += 1
        elif raw == gold:
            stats["guard_killed_correct"] += 1
            killed.append(item["title"][:70])
        else:
            stats["both_wrong"] += 1

    print(f"{args.split} 集 {len(items)} 条，YEAR 守卫四象限：")
    for key, count in stats.items():
        print(f"  {key:22s} {count}")
    if killed:
        print("误杀案例（守卫有 bug，必须修）：")
        for case in killed[:10]:
            print("  -", case)
        sys.exit(1)


if __name__ == "__main__":
    main()
