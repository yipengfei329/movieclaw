"""对量化后的 ONNX 三输出模型做终检（测试集）。

评估口径：
- NER：**字符 span 精确匹配**（字段、来源、起止全对才算对），逐字段 P/R/F1
  ——这是线上真正消费的粒度，比 token 级指标更严格也更真实；
- media_type / content_type：整条分类准确率 + 错例分布；
- 延迟：单条推理 p50/p95（CPU，int8）。

    ml/.venv/bin/python ml/torrent_ner/evaluate.py
    ml/.venv/bin/python ml/torrent_ner/evaluate.py --split dev   # 调试时用
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import onnxruntime as ort
from transformers import AutoTokenizer

from torrent_ner.dataio import load_split
from torrent_ner.encoding import spans_from_char_tags
from torrent_ner.labels import CONTENT_TYPES, FIELDS, ID2LABEL, MAX_LENGTH, MEDIA_TYPES


def predict(session, tokenizer, title: str, subtitle: str) -> tuple[set, str, str]:
    """跑一条推理 → (span 集合, media_type, content_type)。"""
    enc = tokenizer(
        title,
        subtitle or " ",
        truncation=True,
        max_length=MAX_LENGTH,
        return_offsets_mapping=True,
        return_tensors="np",
    )
    inputs = {
        name: enc[name].astype(np.int64)
        for name in (i.name for i in session.get_inputs())
        if name in enc
    }
    span_logits, media_logits, content_logits = session.run(None, inputs)
    pred_ids = span_logits[0].argmax(axis=-1)

    # token 预测投影回字符级标签，再解码成 span（与训练对齐逻辑互逆）
    texts = (title, subtitle or " ")
    char_tags = [["O"] * len(texts[0]), ["O"] * len(texts[1])]
    offsets = enc["offset_mapping"][0]
    for i, seq_id in enumerate(enc.sequence_ids(0)):
        if seq_id is None:
            continue
        start, end = int(offsets[i][0]), int(offsets[i][1])
        tag = ID2LABEL[int(pred_ids[i])]
        if tag == "O":
            continue
        field = tag[2:]
        for pos in range(start, min(end, len(char_tags[seq_id]))):
            char_tags[seq_id][pos] = f"B-{field}" if pos == start and tag.startswith("B-") else f"I-{field}"

    spans = set()
    for seq_id, source in enumerate(("title", "subtitle")):
        for field, start, end in spans_from_char_tags(char_tags[seq_id]):
            spans.add((source, field, start, end))
    return (
        spans,
        MEDIA_TYPES[int(media_logits[0].argmax())],
        CONTENT_TYPES[int(content_logits[0].argmax())],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ONNX 多任务模型终检")
    parser.add_argument("--data", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--onnx", default="ml/artifacts/torrent-ner/onnx/model.int8.onnx")
    parser.add_argument("--split", default="test", choices=["test", "dev", "train"])
    parser.add_argument("--show-errors", type=int, default=10, help="最多打印多少条 NER 错例")
    args = parser.parse_args()

    onnx_path = Path(args.onnx)
    if not onnx_path.exists():
        sys.exit(f"{onnx_path} 不存在，先跑 export.py")
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(onnx_path.parent)
    print(f"模型: {onnx_path.name}")

    # clean_only：带 review 标记的记录 span 可能不完整，会把模型正确输出记成"多抽"
    items = load_split(args.data, args.split, clean_only=True)
    print(f"{args.split} 集 {len(items)} 条（已排除 review 标记样本）")

    tp: Counter = Counter()
    fp: Counter = Counter()
    fn: Counter = Counter()
    cls_hit = {"media_type": 0, "content_type": 0}
    cls_errors: Counter = Counter()
    latencies = []
    errors_shown = 0
    for item in items:
        gold = {(s["source"], s["field"], s["start"], s["end"]) for s in item["spans"]}
        t0 = time.perf_counter()
        pred, media, content = predict(session, tokenizer, item["title"], item.get("subtitle", ""))
        latencies.append(time.perf_counter() - t0)

        for span in pred & gold:
            tp[span[1]] += 1
        for span in pred - gold:
            fp[span[1]] += 1
        for span in gold - pred:
            fn[span[1]] += 1
        for axis, value in (("media_type", media), ("content_type", content)):
            truth = item.get(axis, "other")
            if value == truth:
                cls_hit[axis] += 1
            else:
                cls_errors[f"{axis}: 真={truth} 预={value}"] += 1
        if pred != gold and errors_shown < args.show_errors:
            errors_shown += 1
            print(f"\n[NER错例] {item['id']}\n  title: {item['title']}\n  subtitle: {item.get('subtitle', '')[:100]}")
            print(f"  漏: {sorted(gold - pred)}\n  多: {sorted(pred - gold)}")

    print(f"\n== NER（span 精确匹配）==\n{'字段':<14} {'P':>7} {'R':>7} {'F1':>7} {'支持数':>7}")
    for field in FIELDS:
        p = tp[field] / (tp[field] + fp[field]) if tp[field] + fp[field] else 0.0
        r = tp[field] / (tp[field] + fn[field]) if tp[field] + fn[field] else 0.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        print(f"{field:<14} {p:>7.3f} {r:>7.3f} {f1:>7.3f} {tp[field] + fn[field]:>7}")

    print("\n== 整条分类 ==")
    for axis in ("media_type", "content_type"):
        print(f"{axis}: 准确率 {cls_hit[axis] / len(items):.3f}")
    if cls_errors:
        print("分类错例分布（前 8）：")
        for pattern, count in cls_errors.most_common(8):
            print(f"  {pattern} × {count}")

    lat = sorted(latencies)
    print(
        f"\n延迟(单条, CPU int8): p50={lat[len(lat) // 2] * 1000:.1f}ms "
        f"p95={lat[int(len(lat) * 0.95)] * 1000:.1f}ms"
    )


if __name__ == "__main__":
    main()
