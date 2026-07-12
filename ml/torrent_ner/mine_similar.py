"""以错例为种子的相似样本挖掘——线上坏例 → 池子同类样本 → 定向补训。

两路合流：
1. 相似度检索：字符 2-4gram TF-IDF 余弦近邻，每个种子取 top-K。字符级 n-gram
   对"官方 中字 星际穿越"这种标签堆叠格式的敏感度远高于词级；
2. 模糊模式桶：从种子归纳出的正则模式（标签前缀堆叠/合集/花絮）。

    ml/.venv/bin/python ml/torrent_ner/mine_similar.py \\
        --seeds ml/data/regression/cases.jsonl --out ml/data/raw/samples_round5.jsonl
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.dataio import read_jsonl

# 从错例归纳的模式桶（名称, 正则, 配额）
TAG_WORDS = "官方|国语|國語|中字|限转|限轉|禁转|禁轉|独占|獨佔|特效|应求|應求|首发|首發|DIY|粤语|粵語|双语|雙語|杜比|高清|活[动動]置[顶頂]"
# scope=subtitle 的桶只在副标题上匹配（^ 锚点对拼接文本无意义）
PATTERN_QUOTAS = [
    ("标签堆叠贴片名", re.compile(rf"^\W*(?:(?:{TAG_WORDS})\s+){{2,}}[一-鿿]"), 300, "subtitle"),
    ("合集", re.compile(r"合[集辑輯]|Collection", re.I), 150, "text"),
    ("花絮/特典", re.compile(r"花絮|EXTRAS|特典|Extras"), 100, "text"),
]
KNN_PER_SEED = 40


def main() -> None:
    parser = argparse.ArgumentParser(description="错例相似样本挖掘")
    parser.add_argument("--seeds", default="ml/data/regression/cases.jsonl")
    parser.add_argument("--pool", default="ml/data/sources/external_pool.jsonl.gz")
    parser.add_argument("--annotated", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--out", default="ml/data/raw/samples_round5.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    seeds = read_jsonl(args.seeds)
    annotated = {r["id"] for r in read_jsonl(args.annotated)}
    with gzip.open(args.pool, "rt", encoding="utf-8") as f:
        pool = [json.loads(l) for l in f if l.strip()]
    wild = [r for r in pool if r["id"] not in annotated]
    texts = [r["title"] + " " + r.get("subtitle", "") for r in wild]
    rng = random.Random(args.seed)
    picked: dict[str, dict] = {}

    # 1) TF-IDF 字符 n-gram 近邻
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=200_000)
    matrix = vectorizer.fit_transform(texts)
    seed_vecs = vectorizer.transform(
        [s["title"] + " " + s.get("subtitle", "") for s in seeds]
    )
    sims = cosine_similarity(seed_vecs, matrix)
    for row, seed in zip(sims, seeds):
        top = row.argsort()[::-1][:KNN_PER_SEED]
        for idx in top:
            picked[wild[idx]["id"]] = wild[idx]
        print(f"种子[{seed['id']}] 近邻相似度 {row[top[0]]:.2f}~{row[top[-1]]:.2f}")
    print(f"相似度检索入队 {len(picked)} 条")

    # 2) 模式桶
    for name, pattern, quota, scope in PATTERN_QUOTAS:
        hits = [
            r for r, t in zip(wild, texts)
            if r["id"] not in picked
            and pattern.search(r.get("subtitle", "") if scope == "subtitle" else t)
        ]
        take = hits if len(hits) <= quota else rng.sample(hits, quota)
        for item in take:
            picked[item["id"]] = item
        print(f"桶[{name}] 命中 {len(hits)}，入队 {len(take)}")

    # 3) 错例种子本身也入训练队列（去掉 expect/bug 元数据）
    for s in seeds:
        picked[s["id"]] = {"id": s["id"], "title": s["title"], "subtitle": s.get("subtitle", "")}

    queue = [{"id": r["id"], "title": r["title"], "subtitle": r.get("subtitle", "")} for r in picked.values()]
    queue.sort(key=lambda s: s["id"])
    rng.shuffle(queue)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for row in queue:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"挖掘队列共 {len(queue)} 条 → {out}")


if __name__ == "__main__":
    main()
