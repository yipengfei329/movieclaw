"""合并数据源并分层抽样，生成待标注队列。

数据源：
1. 主项目 site_torrent 表（持续增长的新鲜数据）
2. ml/data/sources/external_pool.jsonl.gz（外部库一次性捐赠的精简池，
   由 extract_source.py 生成，17 个站点 ~7 万条）

按站点分层（各站命名格式差异最大，必须每站都覆盖），站内固定种子随机抽样，
id 去重 + (title, subtitle) 完全重复去重。输出做**确定性乱序**（同输入同种子
则产物稳定）：标注是按文件顺序推进的，乱序保证小批标注也能覆盖所有站点。
只依赖标准库，主项目环境即可运行：

    python ml/torrent_ner/sample.py               # 默认每站 400，约 6-7k 队列
    python ml/torrent_ner/sample.py --per-site 800
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path


def load_site_torrent(db_path: str) -> list[dict]:
    if not Path(db_path).exists():
        print(f"跳过 site_torrent（{db_path} 不存在）")
        return []
    # 只读模式打开，绝不碰生产库的写锁
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT site_id, torrent_id, title, subtitle FROM site_torrent"
    ).fetchall()
    conn.close()
    return [
        {"id": f"{site}:{tid}", "title": title, "subtitle": subtitle or ""}
        for site, tid, title, subtitle in rows
    ]


def load_external_pool(path: str) -> list[dict]:
    if not Path(path).exists():
        print(f"跳过外部池（{path} 不存在）")
        return []
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="合并数据源并分层抽样")
    parser.add_argument("--db", default="data/movieclaw.db", help="主项目 SQLite 路径")
    parser.add_argument(
        "--extra", default="ml/data/sources/external_pool.jsonl.gz", help="外部精简池路径"
    )
    parser.add_argument("--out", default="ml/data/raw/samples.jsonl", help="输出 JSONL 路径")
    parser.add_argument("--per-site", type=int, default=400, help="每站点抽样上限")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（保证可复现）")
    args = parser.parse_args()

    # site_torrent 在前：同 id 冲突时新鲜数据优先
    merged: dict[str, dict] = {}
    seen_text: set[tuple[str, str]] = set()
    for item in load_site_torrent(args.db) + load_external_pool(args.extra):
        key = (item["title"], item.get("subtitle", ""))
        if item["id"] in merged or key in seen_text:
            continue
        merged[item["id"]] = {"id": item["id"], "title": item["title"], "subtitle": item.get("subtitle", "")}
        seen_text.add(key)

    by_site: dict[str, list[dict]] = defaultdict(list)
    for item in merged.values():
        by_site[item["id"].split(":", 1)[0]].append(item)

    rng = random.Random(args.seed)
    samples: list[dict] = []
    for site_id in sorted(by_site):
        pool = sorted(by_site[site_id], key=lambda s: s["id"])
        picked = pool if len(pool) <= args.per_site else rng.sample(pool, args.per_site)
        print(f"站点 {site_id}: 池内 {len(pool)} 条，抽取 {len(picked)} 条")
        samples.extend(picked)

    # 确定性乱序：同输入同种子产物不变，且小批标注天然覆盖所有站点
    samples.sort(key=lambda s: s["id"])
    rng.shuffle(samples)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for sample in samples:
            f.write(json.dumps(sample, ensure_ascii=False) + "\n")
    print(f"共写出 {len(samples)} 条 → {out}")


if __name__ == "__main__":
    main()
