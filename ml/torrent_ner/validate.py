"""标注数据结构校验 + 统计报告。训练前必跑，坏数据在这里拦住。

    python ml/torrent_ner/validate.py
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.dataio import read_jsonl, split_of
from torrent_ner.labels import CONTENT_TYPES, FIELDS, MEDIA_TYPES, SOURCES


def validate_item(item: dict) -> list[str]:
    """返回该条数据的所有结构问题（空列表 = 合法）。"""
    problems = []
    texts = {"title": item.get("title", ""), "subtitle": item.get("subtitle", "")}
    seen: dict[str, list[tuple[int, int]]] = {source: [] for source in SOURCES}

    media_type = item.get("media_type")
    if media_type not in MEDIA_TYPES:
        problems.append(f"media_type 非法或缺失: {media_type!r}")
    if item.get("content_type") not in CONTENT_TYPES:
        problems.append(f"content_type 非法或缺失: {item.get('content_type')!r}")

    for span in item.get("spans", []):
        source, field = span.get("source"), span.get("field")
        start, end = span.get("start"), span.get("end")
        if source not in SOURCES:
            problems.append(f"未知来源 {source!r}")
            continue
        if field not in FIELDS:
            problems.append(f"未知字段 {field!r}")
            continue
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(texts[source])):
            problems.append(f"span 越界: {span}")
            continue
        for other_start, other_end in seen[source]:
            if start < other_end and end > other_start:
                problems.append(f"span 重叠: {source} [{start},{end}) 与 [{other_start},{other_end})")
        seen[source].append((start, end))
        if field == "YEAR":
            # 接受单年 "2025" 或年份区间 "2009-2010"（合集写法，下游取起始年）
            text = texts[source][start:end]
            years = re.split(r"[-~–—]", text)  # 含 en/em dash：站点年份区间写法不统一
            # 下限 1895（电影诞生年）：默片老片在 PT 站真实存在（实测 1920-1929 多条）；
            # 区间尾部允许两位数简写（"2001-02" 即 2001~2002）
            valid = all(
                (y.isdigit() and 1895 <= int(y) <= 2049) or (i > 0 and y.isdigit() and len(y) == 2)
                for i, y in enumerate(years)
            )
            if not valid or len(years) > 2:
                problems.append(f"YEAR 值可疑: {text!r}")
    return problems


def main() -> None:
    parser = argparse.ArgumentParser(description="标注数据校验")
    parser.add_argument("--data", default="ml/data/labeled/annotations.jsonl")
    args = parser.parse_args()

    items = read_jsonl(args.data)
    ids = Counter(item["id"] for item in items)
    duplicated = [sample_id for sample_id, n in ids.items() if n > 1]

    bad = 0
    field_counts: Counter = Counter()
    split_counts: Counter = Counter()
    annotator_counts: Counter = Counter()
    media_counts: Counter = Counter()
    content_counts: Counter = Counter()
    review_count = 0
    negatives = 0
    for item in items:
        problems = validate_item(item)
        if problems:
            bad += 1
            print(f"[问题] {item['id']}: {'; '.join(problems)}")
        for span in item.get("spans", []):
            field_counts[span.get("field")] += 1
        split_counts[split_of(item["id"])] += 1
        annotator_counts[f"{item.get('annotator', '?')} v{item.get('prompt_version', '?')}"] += 1
        media_counts[item.get("media_type")] += 1
        content_counts[item.get("content_type")] += 1
        if item.get("review"):
            review_count += 1
        if not item.get("spans"):
            negatives += 1

    print(f"\n共 {len(items)} 条；结构问题 {bad} 条；重复 id {len(duplicated)} 个；"
          f"待复核 {review_count} 条；负样本（无 span）{negatives} 条")
    print(f"切分分布: {dict(split_counts)}")
    print(f"标注器分布: {dict(annotator_counts)}")
    print(f"media_type 分布: {dict(media_counts)}")
    print(f"content_type 分布: {dict(content_counts)}")
    print("各字段 span 数:")
    for field in FIELDS:
        print(f"  {field:9s} {field_counts.get(field, 0)}")
    if duplicated:
        print(f"重复 id 示例: {duplicated[:5]}")
    if bad or duplicated:
        sys.exit(1)


if __name__ == "__main__":
    main()
