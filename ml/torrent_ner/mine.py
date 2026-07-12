"""错例驱动的定向补样挖掘——生成第二轮标注队列。

三路合流（去重、排除已标注）：
1. 模式配额：按野外验证确凿错例归纳的模式，从池子里按正则捞同类样本；
2. 不确定度挖掘（主动学习）：模型全量推理未标注池，softmax 置信度最低的
   样本就是模型自认的知识盲区，优先标注；
3. 确凿错例：wildcheck 双裁判一致判错的样本直接入队（模型已证明不会）。

    ml/.venv/bin/python ml/torrent_ner/mine.py          # 需要训练环境（跑推理）
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

import numpy as np

from torrent_ner.dataio import read_jsonl
from torrent_ner.labels import MAX_LENGTH

# (名称, 正则, 配额)——配额按错例频度和池内存量拍的，改动无需仪式感
PATTERN_QUOTAS = [
    ("第N期综艺", re.compile(r"第\s?\d+\s?期"), 250),
    ("英文序数季", re.compile(r"\d+(st|nd|rd|th)\s+Season", re.I), 44),
    ("叠字片名", re.compile(r"(.)\1{2,}"), 200),
    ("动画线索", re.compile(r"新番|字幕[组組社]|OVA|剧场版|劇場版|国漫|動畫|动画"), 350),
    ("纪录片线索", re.compile(r"纪录|紀錄|BBC|NHK|Documentary", re.I), 250),
    ("综艺线索", re.compile(r"综艺|綜藝|真人秀|脱口秀|演唱会|演唱會"), 250),
]
UNCERTAIN_QUOTA = 400


def confirmed_error_ids() -> set[str]:
    """wildcheck 双裁判一致判错（任一字段）的样本 id。"""
    judges = {}
    for engine in ("claude", "codex"):
        path = Path(f"ml/data/labeled/wildcheck_verdicts_{engine}.jsonl")
        if path.exists():
            judges[engine] = {r["id"]: r.get("verdicts", {}) for r in read_jsonl(path)}
    if len(judges) < 2:
        return set()
    ids = set()
    for sample_id in set.intersection(*(set(v) for v in judges.values())):
        fields = set.union(*(set(v[sample_id]) for v in judges.values()))
        if any(all(judges[e][sample_id].get(f) == "wrong" for e in judges) for f in fields):
            ids.add(sample_id)
    return ids


def uncertainty_scores(items: list[dict], onnx_path: str) -> list[float]:
    """每条样本的模型置信度分数（越低越不确定）。

    分数 = span 头逐 token 的 top1-top2 概率差的最小值，与两个分类头
    margin 的较小者取 min——任何一个头犹豫都算这条样本可疑。
    """
    import onnxruntime as ort
    from transformers import AutoTokenizer

    session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(Path(onnx_path).parent)
    input_names = {i.name for i in session.get_inputs()}

    def softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
        e = np.exp(x - x.max(axis=axis, keepdims=True))
        return e / e.sum(axis=axis, keepdims=True)

    scores = []
    for n, item in enumerate(items):
        enc = tokenizer(
            item["title"], item.get("subtitle") or " ",
            truncation=True, max_length=MAX_LENGTH, return_tensors="np",
        )
        inputs = {k: enc[k].astype(np.int64) for k in input_names if k in enc}
        span_logits, media_logits, content_logits = session.run(None, inputs)
        probs = softmax(span_logits[0])
        top2 = np.sort(probs, axis=-1)[:, -2:]
        token_margin = float((top2[:, 1] - top2[:, 0]).min())
        cls_margin = min(
            float(np.diff(np.sort(softmax(logits[0]))[-2:])[0])
            for logits in (media_logits, content_logits)
        )
        scores.append(min(token_margin, cls_margin))
        if (n + 1) % 10000 == 0:
            print(f"  推理进度 {n + 1}/{len(items)}")
    return scores


def main() -> None:
    parser = argparse.ArgumentParser(description="定向补样挖掘")
    parser.add_argument("--pool", default="ml/data/sources/external_pool.jsonl.gz")
    parser.add_argument("--annotated", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--onnx", default="ml/artifacts/torrent-ner/onnx/model.int8.onnx")
    parser.add_argument("--out", default="ml/data/raw/samples_round2.jsonl")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    annotated = {r["id"] for r in read_jsonl(args.annotated)}
    with gzip.open(args.pool, "rt", encoding="utf-8") as f:
        pool = [json.loads(l) for l in f if l.strip()]
    wild = [r for r in pool if r["id"] not in annotated]
    rng = random.Random(args.seed)

    picked: dict[str, dict] = {}

    # 1) 确凿错例全量入队
    for item in wild:
        if item["id"] in confirmed_error_ids():
            picked[item["id"]] = item
    print(f"确凿错例入队 {len(picked)} 条")

    # 2) 模式配额
    for name, pattern, quota in PATTERN_QUOTAS:
        hits = [r for r in wild if r["id"] not in picked
                and pattern.search(r["title"] + " " + r.get("subtitle", ""))]
        take = hits if len(hits) <= quota else rng.sample(hits, quota)
        for item in take:
            picked[item["id"]] = item
        print(f"模式[{name}] 命中 {len(hits)}，入队 {len(take)}")

    # 3) 不确定度挖掘（对剩余样本全量推理）
    rest = [r for r in wild if r["id"] not in picked]
    print(f"不确定度挖掘：对剩余 {len(rest)} 条推理打分 ...")
    scores = uncertainty_scores(rest, args.onnx)
    order = np.argsort(scores)[:UNCERTAIN_QUOTA]
    for idx in order:
        picked[rest[idx]["id"]] = rest[idx]
    print(f"不确定度入队 {len(order)} 条（分数区间 {scores[order[0]]:.3f}~{scores[order[-1]]:.3f}）")

    queue = [
        {"id": r["id"], "title": r["title"], "subtitle": r.get("subtitle", "")}
        for r in picked.values()
    ]
    queue.sort(key=lambda s: s["id"])
    rng.shuffle(queue)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for row in queue:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"第二轮队列共 {len(queue)} 条 → {out}")


if __name__ == "__main__":
    main()
