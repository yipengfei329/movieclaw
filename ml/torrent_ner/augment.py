"""结构化合成增强：教模型"某类文本变换不改变（或确定性改变）标签"。

四种模式（全部是机械变换，标签绝对正确，零标注成本）：
- decoration     副标题前拼装饰前缀（*活動置頂N* 等），span 整体平移
- trailing_stop  中文片名 span 之后插入句号"。"，span 不含句号——治"你的名字。"
- version_suffix 片名 span 之后插入" IMAX版/加长版/…"，span 不含版本词
- chapter        片名 span 之后插入" 第N章"，并为其添加 EPISODE 标签
另有 collection 过采样：真实 collection 标注太稀有（<100 条），复制并施加
decoration 变换扩充分类头的监督信号。

产物写独立文件，训练时经 train.py --extra-train 只进训练集、绝不进 dev/test。

    python ml/torrent_ner/augment.py          # 零依赖
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.dataio import read_jsonl

DECORATIONS = [
    "*活動置頂{n}*", "*活动置顶{n}*", "【活动】", "【置顶】", "置顶 ",
    "*杜比专区*", "【官方活动】", "*限时优惠*", "[应求] ", "【首发】",
]
VERSION_WORDS = [" IMAX版", " 加长版", " 导演剪辑版", " 未删减版", " 修复版", " 完整版", " 典藏版"]
CN_NUMS = "一二三四五六七八九十"


def shift_spans(spans: list[dict], source: str, pos: int, delta: int) -> list[dict]:
    """指定来源中 pos 之后的 span 整体平移 delta。"""
    out = []
    for s in spans:
        if s["source"] == source and s["start"] >= pos:
            out.append({**s, "start": s["start"] + delta, "end": s["end"] + delta})
        else:
            out.append(dict(s))
    return out


def pick_zh_span(record: dict) -> dict | None:
    """选一个副标题里的中文片名 span 作为插入锚点。"""
    candidates = [
        s for s in record["spans"]
        if s["source"] == "subtitle" and s["field"] == "TITLE_ZH" and s["end"] - s["start"] >= 2
    ]
    return candidates[0] if candidates else None


def make_decoration(record: dict, rng: random.Random) -> dict:
    deco = rng.choice(DECORATIONS).format(n=rng.randrange(100, 300))
    return {
        **record,
        "subtitle": deco + record["subtitle"],
        "spans": shift_spans(record["spans"], "subtitle", 0, len(deco)),
    }


def make_insertion(record: dict, rng: random.Random, mode: str) -> dict | None:
    """在中文片名 span 之后插入文本；span 不吞插入内容。"""
    anchor = pick_zh_span(record)
    if anchor is None:
        return None
    pos = anchor["end"]
    if mode == "trailing_stop":
        ins = "。"
        extra = None
    elif mode == "version_suffix":
        ins = rng.choice(VERSION_WORDS)
        extra = None
    else:  # chapter
        expr = f"第{rng.choice(CN_NUMS)}章"
        ins = " " + expr
        # 章节表达按 v10 规范入 EPISODE
        extra = {"source": "subtitle", "field": "EPISODE", "start": pos + 1, "end": pos + 1 + len(expr)}
    spans = shift_spans(record["spans"], "subtitle", pos, len(ins))
    if extra:
        spans.append(extra)
        spans.sort(key=lambda s: (s["source"], s["start"]))
    return {**record, "subtitle": record["subtitle"][:pos] + ins + record["subtitle"][pos:], "spans": spans}


def main() -> None:
    parser = argparse.ArgumentParser(description="结构化合成增强")
    parser.add_argument("--data", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--out", default="ml/data/labeled/augmented.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    records = [r for r in read_jsonl(args.data) if r["spans"] and not r.get("review")]
    base = [r for r in records if r.get("subtitle", "").strip()]
    collections = [r for r in records if r.get("media_type") == "collection"]

    plan = [("decoration", 300), ("trailing_stop", 250), ("version_suffix", 250), ("chapter", 200)]
    rows = []
    for mode, quota in plan:
        made = 0
        for record in rng.sample(base, len(base)):
            if made >= quota:
                break
            out = make_decoration(record, rng) if mode == "decoration" else make_insertion(record, rng, mode)
            if out is None:
                continue
            rows.append({**out, "id": f"aug-{mode}:{record['id']}",
                         "annotator": f"synthetic:{mode}"})
            made += 1
        print(f"{mode}: 合成 {made} 条")

    # collection 过采样 ×2（施加 decoration 变换避免完全重复）
    for i in range(2):
        for record in collections:
            rows.append({**make_decoration(record, rng), "id": f"aug-collection{i}:{record['id']}",
                         "annotator": "synthetic:collection-oversample"})
    print(f"collection 过采样: {len(collections) * 2} 条")

    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"共 {len(rows)} 条 → {args.out}")


if __name__ == "__main__":
    main()
