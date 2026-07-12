"""标注数据的读写与切分。

数据格式（JSONL，每行一条）：
{
  "id": "chdbits:12345",          # site_id:torrent_id，全局唯一
  "title": "英文种子名",
  "subtitle": "中文副标题",
  "spans": [                       # 字符级标注，start/end 是半开区间
    {"source": "title", "field": "TITLE_EN", "start": 0, "end": 12},
    ...
  ],
  "review": ["..."]                # 可选：标注器留下的待人工复核原因
}

切分策略：按 id 的稳定哈希分桶（80/10/10），保证多次重训、增量补数据时
同一条样本永远落在同一集合——测试集不被污染是长期迭代的生命线。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def read_jsonl(path: str | Path) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {line_no} 行不是合法 JSON: {exc}") from exc
    return items


def append_jsonl(path: str | Path, items: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def split_of(sample_id: str) -> str:
    """稳定哈希切分：返回 train / dev / test。"""
    bucket = int(hashlib.sha1(sample_id.encode("utf-8")).hexdigest(), 16) % 10
    if bucket < 8:
        return "train"
    return "dev" if bucket == 8 else "test"


def load_split(path: str | Path, split: str, clean_only: bool = False) -> list[dict]:
    """加载指定切分的数据。

    clean_only=True 时剔除带 ``review`` 标记的记录——这些是标注器抽出的子串
    无法在原文定位（幻觉/改写）的样本，其成功定位的 span 往往**不完整**：某
    字段抽取失败就等于该字段被留空，直接拿去训练会把「抽取失败」当成「无此
    实体」，教模型漏抽（假阴性）。因此训练/评估传 True，把它们挡在门外，留在
    文件里等人工修正；人工清掉 review 标记后自动重新入训。统计场景传 False。
    """
    items = [item for item in read_jsonl(path) if split_of(item["id"]) == split]
    if clean_only:
        items = [item for item in items if not item.get("review")]
    return items
