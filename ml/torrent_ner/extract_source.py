"""从外部种子库（一次性数据捐赠）抽取精简训练池。

设计：
- 只取训练需要的字段（站点/ID/种子名/副标题 + 类别、imdb_id 两个未来有用的
  附带真值），下载链接等敏感字段一概不碰；
- 每站点水库抽样（reservoir sampling）设上限，把百兆级源库压成几 MB 的池子，
  抽完源库即可删除；
- 容忍源库页损坏：按 rowid 分窗扫描，坏窗口二分缩小后跳过，能救多少救多少。

    python ml/torrent_ner/extract_source.py --db ml/data/sources/site_data.db
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sqlite3
from collections import defaultdict
from pathlib import Path

QUERY = "SELECT site_id, id, name, subject, cate_level1, imdb_id FROM torrent_info WHERE rowid >= ? AND rowid < ?"


def scan_window(conn, start: int, end: int, on_row, min_window: int = 128) -> int:
    """扫描 [start, end) 的 rowid 窗口；遇坏页二分缩小，返回丢弃的窗口数。"""
    try:
        for row in conn.execute(QUERY, (start, end)):
            on_row(row)
        return 0
    except sqlite3.DatabaseError:
        if end - start <= min_window:
            return 1
        mid = (start + end) // 2
        return scan_window(conn, start, mid, on_row) + scan_window(conn, mid, end, on_row)


def probe_max_rowid(conn, cap: int = 1 << 28) -> int:
    """倍增探测 rowid 上界。MAX(rowid) 会撞坏页，这里只做点查询；
    查询报错时按"可能还有数据"处理继续翻倍——高估无害（空窗扫描近零成本）。"""
    hi = 1
    while hi < cap:
        try:
            row = conn.execute(
                "SELECT rowid FROM torrent_info WHERE rowid >= ? LIMIT 1", (hi,)
            ).fetchone()
            if row is None:
                return hi
            hi = max(row[0], hi) * 2
        except sqlite3.DatabaseError:
            hi *= 2
    return cap


def main() -> None:
    parser = argparse.ArgumentParser(description="外部种子库 → 精简训练池")
    parser.add_argument("--db", default="ml/data/sources/site_data.db")
    parser.add_argument("--out", default="ml/data/sources/external_pool.jsonl.gz")
    parser.add_argument("--per-site", type=int, default=5000, help="每站点保留上限")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    max_rowid = probe_max_rowid(conn)
    print(f"rowid 上界估计: {max_rowid}")

    rng = random.Random(args.seed)
    reservoir: dict[str, list] = defaultdict(list)
    seen: dict[str, int] = defaultdict(int)

    def on_row(row) -> None:
        site_id, tid, name, subject, category, imdb_id = row
        if not name or not str(name).strip():
            return
        seen[site_id] += 1
        item = (site_id, tid, name, subject, category, imdb_id)
        if len(reservoir[site_id]) < args.per_site:
            reservoir[site_id].append(item)
        else:
            j = rng.randrange(seen[site_id])
            if j < args.per_site:
                reservoir[site_id][j] = item

    window, dropped = 20000, 0
    for start in range(1, max_rowid + 1, window):
        dropped += scan_window(conn, start, min(start + window, max_rowid + 1), on_row)
    conn.close()

    pool = []
    for site_id in sorted(reservoir):
        print(f"站点 {site_id}: 可读 {seen[site_id]} 条，保留 {len(reservoir[site_id])} 条")
        for site, tid, name, subject, category, imdb_id in reservoir[site_id]:
            record = {"id": f"{site}:{tid}", "title": str(name), "subtitle": str(subject or "")}
            if category:
                record["category"] = str(category)
            if imdb_id:
                record["imdb_id"] = str(imdb_id)
            pool.append(record)
    pool.sort(key=lambda r: r["id"])

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for record in pool:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    size_mb = out.stat().st_size / 1024 / 1024
    print(f"\n共保留 {len(pool)} 条 → {out}（{size_mb:.1f} MB），跳过损坏窗口 {dropped} 个")
    print("确认池子无误后，源 db 可以删除。")


if __name__ == "__main__":
    main()
