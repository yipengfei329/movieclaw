"""野外验证：从未标注的池子数据上跑模型，双 LLM 裁判验证抽取准确性。

与 evaluate.py（测试集 vs 金标）的本质区别：测试集金标出自 codex 标注器，
标注器的系统性偏见在测试指标里不可见（考官=出题人）。这里用**从未参与
标注**的样本 + 独立裁判直接审模型输出，测的是无偏泛化。裁判做验证不做
重抽取——验证比生成容易，裁判可靠性更高。

    1. ml/.venv/bin/python ml/torrent_ner/wildcheck.py parse --n 300   # 抽样+模型解析
    2. python ml/torrent_ner/wildcheck.py judge --engine claude        # 裁判1（主项目环境即可）
       python ml/torrent_ner/wildcheck.py judge --engine codex        # 裁判2
    3. python ml/torrent_ner/wildcheck.py report                       # 汇总
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.annotate import ENGINES, parse_json_array
from torrent_ner.dataio import append_jsonl, read_jsonl

POOL = "ml/data/sources/external_pool.jsonl.gz"
ANNOTATED = "ml/data/labeled/annotations.jsonl"
PARSES = "ml/data/labeled/wildcheck_parses.jsonl"
VERDICT_TMPL = "ml/data/labeled/wildcheck_verdicts_{engine}.jsonl"

PARSE_FIELDS = ("title_zh", "title_en", "year", "season", "episode", "episode_total",
                "media_type", "content_type")

JUDGE_PROMPT = """\
你是影视种子解析结果的质检员。下面每条包含种子原文（title/subtitle）和一个抽取模型的解析结果 parse。
逐条逐字段判断解析是否正确，宽严标准：
- 语义正确即可：别名多列/少列一个不算错，除非漏了主片名或抽出了不存在的内容；
- 片名边界差一两个装饰字符不算错，但截断一半、混入技术词/年份/组名算错；
- year/season/episode/episode_total 值错、张冠李戴（当前集当成总集数）算错；
- media_type: movie=单部影片, series=分集作品, other=非影视；content_type: anime/documentary/variety/music/other（普通真人影视=other）；
- 非影视内容（软件/音乐专辑/体育/MV）所有抽取字段应为空，media_type=other。

只输出 JSON 数组，每个元素：
{"id": "...", "verdicts": {"title_zh": "ok|wrong", "title_en": "ok|wrong", "year": "ok|wrong", "season": "ok|wrong", "episode": "ok|wrong", "episode_total": "ok|wrong", "media_type": "ok|wrong", "content_type": "ok|wrong"}, "note": "有 wrong 时一句话说明"}

待质检数据：
"""


def cmd_parse(args) -> None:
    """抽样 + 模型解析（需要训练环境跑 onnx 推理）。"""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    from torrent_ner.evaluate import predict

    annotated_ids = {r["id"] for r in read_jsonl(ANNOTATED)}
    with gzip.open(args.pool, "rt", encoding="utf-8") as f:
        pool = [json.loads(l) for l in f if l.strip()]
    wild = [r for r in pool if r["id"] not in annotated_ids]
    picked = random.Random(args.seed).sample(wild, min(args.n, len(wild)))
    print(f"池子 {len(pool)} 条，未标注 {len(wild)} 条，抽取 {len(picked)} 条")

    session = ort.InferenceSession(args.onnx, providers=["CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(Path(args.onnx).parent)

    out = Path(args.out)
    out.unlink(missing_ok=True)
    rows = []
    for item in picked:
        title, subtitle = item["title"], item.get("subtitle", "")
        spans, media, content = predict(session, tokenizer, title, subtitle)
        texts = {"title": title, "subtitle": subtitle}
        parse: dict = {f: [] for f in ("title_zh", "title_en", "season", "episode", "episode_total")}
        parse["year"] = None
        field_key = {"TITLE_ZH": "title_zh", "TITLE_EN": "title_en", "SEASON": "season",
                     "EPISODE": "episode", "EPISODE_TOTAL": "episode_total"}
        for source, field, start, end in sorted(spans):
            value = texts[source][start:end]
            if field == "YEAR":
                parse["year"] = parse["year"] or value
            else:
                key = field_key[field]
                if value not in parse[key]:
                    parse[key].append(value)
        parse["media_type"], parse["content_type"] = media, content
        rows.append({"id": item["id"], "title": title, "subtitle": subtitle, "parse": parse})
    append_jsonl(out, rows)
    print(f"模型解析完成 → {out}")


def cmd_judge(args) -> None:
    """LLM 裁判逐字段验证（断点续判，与 annotate 相同的稳定性语义）。"""
    call_model = ENGINES[args.engine]
    out = Path(VERDICT_TMPL.format(engine=args.engine))
    done = {r["id"] for r in read_jsonl(out)} if out.exists() else set()
    todo = [r for r in read_jsonl(PARSES) if r["id"] not in done]
    print(f"待裁判 {len(todo)} 条（已完成 {len(done)} 条），裁判 {args.engine}")

    failures = 0
    for i in range(0, len(todo), args.batch):
        batch = todo[i : i + args.batch]
        payload = json.dumps(batch, ensure_ascii=False, indent=1)
        try:
            verdicts = parse_json_array(call_model(JUDGE_PROMPT + payload, None))
        except Exception as exc:  # 与 annotate 同理：跳过可重跑，连续失败熔断
            failures += 1
            print(f"批次失败({failures}): {str(exc)[:200]}")
            if failures >= 3:
                sys.exit("连续失败熔断，修复后重跑自动续接")
            continue
        failures = 0
        by_id = {v.get("id"): v for v in verdicts if isinstance(v, dict)}
        rows = [
            {"id": r["id"], "verdicts": by_id[r["id"]].get("verdicts", {}),
             "note": by_id[r["id"]].get("note", "")}
            for r in batch if r["id"] in by_id
        ]
        append_jsonl(out, rows)
        print(f"进度 {min(i + args.batch, len(todo))}/{len(todo)}")
    print(f"完成 → {out}")


def cmd_report(args) -> None:
    parses = {r["id"]: r for r in read_jsonl(PARSES)}
    judges: dict[str, dict] = {}
    for engine in ("claude", "codex"):
        path = Path(VERDICT_TMPL.format(engine=engine))
        if path.exists():
            judges[engine] = {r["id"]: r for r in read_jsonl(path)}
    if not judges:
        sys.exit("还没有裁判结果，先跑 judge")

    print(f"样本 {len(parses)} 条，裁判: {', '.join(judges)}\n")
    print(f"{'字段':<14}" + "".join(f"{e + ' OK率':>14}" for e in judges) + f"{'双判皆错':>10}")
    both_wrong_ids: dict[str, list[str]] = {}
    for field in PARSE_FIELDS:
        rates = []
        for engine, verdicts in judges.items():
            judged = [v for v in verdicts.values() if field in v.get("verdicts", {})]
            ok = sum(1 for v in judged if v["verdicts"][field] == "ok")
            rates.append(f"{ok / len(judged):.1%}" if judged else "-")
        both = [
            sample_id for sample_id in parses
            if len(judges) == 2
            and all(judges[e].get(sample_id, {}).get("verdicts", {}).get(field) == "wrong" for e in judges)
        ]
        both_wrong_ids[field] = both
        print(f"{field:<14}" + "".join(f"{r:>14}" for r in rates) + f"{len(both):>10}")

    confirmed = sorted({i for ids in both_wrong_ids.values() for i in ids})
    if confirmed:
        print(f"\n双裁判一致判错的样本（确凿错例，共 {len(confirmed)} 条，示例前 {args.show}）：")
        for sample_id in confirmed[: args.show]:
            r = parses[sample_id]
            notes = " / ".join(
                f"{e}:{judges[e].get(sample_id, {}).get('note', '')}" for e in judges
            )
            print(f"- {sample_id}\n    T: {r['title'][:80]}\n    解析: {json.dumps(r['parse'], ensure_ascii=False)[:120]}\n    裁判: {notes[:160]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="野外验证（模型解析 + 双 LLM 裁判）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("parse")
    p.add_argument("--pool", default=POOL)
    p.add_argument("--onnx", default="ml/artifacts/torrent-ner/onnx/model.int8.onnx")
    p.add_argument("--out", default=PARSES)
    p.add_argument("--n", type=int, default=300)
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("judge")
    p.add_argument("--engine", choices=sorted(ENGINES), required=True)
    p.add_argument("--batch", type=int, default=10)
    p.set_defaults(func=cmd_judge)

    p = sub.add_parser("report")
    p.add_argument("--show", type=int, default=10)
    p.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
