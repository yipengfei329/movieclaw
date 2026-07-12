"""装饰词合成增强：教模型"发布装饰不改变任何标签"的不变性。

从干净标注样本合成变体：副标题前拼接装饰前缀，副标题 span 整体平移，
其余标签原样保留——零标注成本，专治装饰词干扰（*活動置頂N* 等真实样本
全池只有 23 条，不够学）。

产物写独立文件（不混入人工标注主文件），训练时经 train.py --extra-train
只进训练集、绝不进 dev/test（合成数据参与评估会虚高指标）。

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

# 装饰前缀词表：来自真实站点观察（ttg 活动置顶、通用发布标签）
DECORATIONS = [
    "*活動置頂{n}*", "*活动置顶{n}*", "【活动】", "【置顶】", "置顶 ",
    "*杜比专区*", "【官方活动】", "*限时优惠*", "[应求] ", "【首发】",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="装饰词合成增强")
    parser.add_argument("--data", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--out", default="ml/data/labeled/augmented.jsonl")
    parser.add_argument("--n", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    # 只用带副标题、有 span、无 review 的干净样本做底料
    base = [
        r for r in read_jsonl(args.data)
        if r.get("subtitle", "").strip() and r["spans"] and not r.get("review")
    ]
    picked = rng.sample(base, min(args.n, len(base)))

    rows = []
    for record in picked:
        deco = rng.choice(DECORATIONS).format(n=rng.randrange(100, 300))
        shift = len(deco)
        spans = [
            {**s, "start": s["start"] + shift, "end": s["end"] + shift}
            if s["source"] == "subtitle" else dict(s)
            for s in record["spans"]
        ]
        rows.append({
            "id": f"aug:{record['id']}",
            "title": record["title"],
            "subtitle": deco + record["subtitle"],
            "spans": spans,
            "media_type": record.get("media_type", "other"),
            "content_type": record.get("content_type", "other"),
            "annotator": "synthetic:decoration-aug",
            "prompt_version": record.get("prompt_version", 0),
        })

    with open(args.out, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"合成 {len(rows)} 条 → {args.out}")


if __name__ == "__main__":
    main()
