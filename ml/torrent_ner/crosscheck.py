"""双引擎交叉质检：对比两份标注文件在共同 id 上的一致性。

用法（README「双引擎交叉质检」守则的工具化）：
1. 从主标注文件抽样生成复标输入：
     python ml/torrent_ner/crosscheck.py sample --n 300
2. 用另一个引擎复标到独立文件：
     python ml/torrent_ner/annotate.py --engine claude \\
         --in ml/data/labeled/crosscheck_input.jsonl --out ml/data/labeled/crosscheck.jsonl
3. 出一致性报告（分歧样本人工裁决，谁错修谁）：
     python ml/torrent_ner/crosscheck.py report
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.dataio import read_jsonl
from torrent_ner.labels import FIELDS

MAIN = "ml/data/labeled/annotations.jsonl"
CC_INPUT = "ml/data/labeled/crosscheck_input.jsonl"
CC_OUT = "ml/data/labeled/crosscheck.jsonl"


def span_set(item: dict, field: str) -> frozenset:
    return frozenset(
        (s["source"], s["start"], s["end"]) for s in item.get("spans", []) if s["field"] == field
    )


def cmd_sample(args) -> None:
    items = [i for i in read_jsonl(args.main) if not i.get("review")]
    picked = random.Random(args.seed).sample(items, min(args.n, len(items)))
    with open(args.out, "w", encoding="utf-8") as f:
        for item in picked:
            row = {"id": item["id"], "title": item["title"], "subtitle": item.get("subtitle", "")}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"抽出 {len(picked)} 条复标输入 → {args.out}")


def cmd_report(args) -> None:
    main_by_id = {i["id"]: i for i in read_jsonl(args.main)}
    cross = [i for i in read_jsonl(args.cross) if i["id"] in main_by_id]
    if not cross:
        sys.exit("交叉文件与主文件无共同 id，先跑 sample + annotate")

    agree: Counter = Counter()
    total: Counter = Counter()
    disagreements: list[tuple[str, str]] = []
    for cc_item in cross:
        main_item = main_by_id[cc_item["id"]]
        for field in FIELDS:
            total[field] += 1
            if span_set(main_item, field) == span_set(cc_item, field):
                agree[field] += 1
            else:
                disagreements.append((cc_item["id"], field))
        for axis in ("media_type", "content_type"):
            total[axis] += 1
            if main_item.get(axis) == cc_item.get(axis):
                agree[axis] += 1
            else:
                disagreements.append((cc_item["id"], axis))

    print(f"交叉样本 {len(cross)} 条，逐字段一致率：")
    for key in list(FIELDS) + ["media_type", "content_type"]:
        rate = agree[key] / total[key] if total[key] else 1.0
        print(f"  {key:14s} {rate:6.1%}  （分歧 {total[key] - agree[key]} 条）")

    if disagreements:
        print(f"\n分歧明细（前 {args.show} 条，人工裁决后修主文件或复标文件）：")
        for sample_id, key in disagreements[: args.show]:
            main_item, cc_item = main_by_id[sample_id], next(
                i for i in cross if i["id"] == sample_id
            )
            print(f"- {sample_id} [{key}]")
            print(f"    T: {main_item['title'][:80]}")
            print(f"    S: {main_item.get('subtitle', '')[:80]}")
            if key in ("media_type", "content_type"):
                print(f"    主={main_item.get(key)}  交叉={cc_item.get(key)}")
            else:
                texts = {"title": main_item["title"], "subtitle": main_item.get("subtitle", "")}
                for name, item in (("主", main_item), ("交叉", cc_item)):
                    values = [
                        repr(texts[s["source"]][s["start"] : s["end"]])
                        for s in item.get("spans", [])
                        if s["field"] == key
                    ]
                    print(f"    {name}={values}")


def main() -> None:
    parser = argparse.ArgumentParser(description="双引擎交叉质检")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_sample = sub.add_parser("sample", help="从主标注抽样生成复标输入")
    p_sample.add_argument("--main", default=MAIN)
    p_sample.add_argument("--out", default=CC_INPUT)
    p_sample.add_argument("--n", type=int, default=300)
    p_sample.add_argument("--seed", type=int, default=42)
    p_sample.set_defaults(func=cmd_sample)

    p_report = sub.add_parser("report", help="对比主/交叉标注出一致性报告")
    p_report.add_argument("--main", default=MAIN)
    p_report.add_argument("--cross", default=CC_OUT)
    p_report.add_argument("--show", type=int, default=15)
    p_report.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
