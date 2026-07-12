"""用大模型 CLI 无头模式批量蒸馏标注，产出字符级 span 标注数据。

支持两个引擎（同一提示词、同一解析逻辑，标注规范保持一致）：
- claude：调 `claude -p`（默认 claude-sonnet-5）
- codex： 调 `codex exec`（默认走 ~/.codex/config.toml 的 model，当前 gpt-5.6-sol）

设计：让大模型只负责"抽取原文子串"（它擅长），字符偏移由本脚本用字符串
定位计算（代码擅长）——绝不让模型报数字偏移。定位失败的样本带 review
标记落盘，后续人工复核；这批"模型和定位器意见不合"的样本恰是标注价值
最高的病例。

断点续标：输出文件里已有的 id 自动跳过，可随时中断重跑；两个引擎写同一
输出文件，也可各标一部分（双引擎交叉验证见 README 迭代守则）。

    python ml/torrent_ner/annotate.py                        # claude 全量
    python ml/torrent_ner/annotate.py --engine codex --limit 50   # codex 小批试跑
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from torrent_ner.dataio import append_jsonl, read_jsonl
from torrent_ner.encoding import resolve_overlaps
from torrent_ner.labels import CONTENT_TYPES, MEDIA_TYPES

# 标注规范版本：随每条记录落盘。改提示词必须 +1，训练前可按版本过滤/重标。
# v2：新增"零编造"强约束（强化既有的逐字子串要求，未改判定规则，故 v1 数据仍兼容）
# v3：episode 拆成 episode（当前集）+ episode_total（总集数）——标签集变了，
#     v1/v2 的 episode 标注属旧 schema，全量标注前应清空重标（见 README）
# v4：新增整条分类 media_type（movie/series/other）——document 级判定，非 span
# v5：新增内容轴 content_type（真人/动漫/综艺/纪录片/其他），与 media_type 正交
# v6：content_type 精简为 4 类，去掉 live_action（普通真人影视归 other，靠 media_type 识别）
# v7：content_type 增加 music（专辑/MV/演唱会）——PT 无损音乐区是大类，从 other 分出
# v8：交叉质检暴露的两个规范空白（增量澄清，v7 未定义的行为，存量 v7 数据无需重标）：
#     ①成人影片：视为影视，番号作 title_en 抽出，演员名不抽，movie/other；
#     ②综艺子栏目/特辑名（"训练日记"等）不算别名，不入 title_zh
# v9：线上错例暴露的三条边界规则（增量澄清）：①发布标签词绝不入片名；
#     ②合集名整体抽取不截断（含人名）；③花絮/EXTRAS 只抽正片名
# v10：①media_type 新增 collection（多部独立作品打包）——schema 变更，存量
#     合集类样本须重标（其余样本的 movie/series/other 语义不变）；
#     ②版本描述词（IMAX版/加长版/导演剪辑…）不入片名
PROMPT_VERSION = 10

# 标注规范：改动此提示词 = 改动标注标准，须 PROMPT_VERSION+1 并全量重标或版本隔离
PROMPT_HEADER = """\
你是影视种子命名解析专家。下面是若干 PT 站种子，每条有英文种子名 title 和中文副标题 subtitle。
对每条抽取以下信息，全部必须是 title 或 subtitle 中**逐字连续出现的子串**（保留点号、空格等原始写法），抽不到就给空值：

- title_en: 外文片名及外文别名列表（不含年份、分辨率等技术词、压制组；"Working.Girls.1986.1080p..." 取 "Working.Girls"）
- title_zh: 中文片名及所有中文别名列表（含港台译名，逐个列出；不含季/集字样，"神墓 第三季" 只取 "神墓"）
- year: 发行年份字样（如 "2025"）。年份仅作为片名一部分出现时（如 Reply 1988 无真实年份）给 null
- season: 季数表达列表（如 "S01"、"第三季"；"S01E10" 拆出 "S01"）
- episode: **当前集号 / 集号区间**列表（如 "E10"、"第50集"、"EP12"、"E01-E12"；"S01E10" 拆出 "E10"）——指这个种子是第几集
- episode_total: **总集数 / 完结合集**列表（如 "全12集"、"12集全"、"全26话"）——指这是共 N 集的合集。判断依据是"全/共/…全"等完结语，与 episode 语义不同，别混

另外给整条种子两个类别（这不是子串，是对整体的判断；两者相互独立，各判各的）：
- media_type（结构轴）: "movie" | "series" | "collection" | "other" 四选一
    · movie      单部影片：电影、剧场版、纪录片单片
    · series     分集连续作品：电视剧、剧集、综艺、番剧；**同一作品**的多季/全集打包也算
    · collection **多部独立作品的打包**："六部合集"、导演电影合集、系列电影全集
      （X战警六部曲）、混装作品包——判据是"包含多部各自独立的作品"
    · other      非影视：软件、音乐专辑、体育赛事、游戏、电子书、MV
  判定依据是**作品结构**，不是题材。collection 的片名 = 合集名整体。
- content_type（内容轴）: "anime" | "documentary" | "variety" | "music" | "other" 五选一
    · anime       动画/动漫（番、剧场版、OVA、字幕社、日本动画等线索）
    · documentary 纪录片（纪录、探索、BBC、NHK 等）
    · variety     综艺/真人秀（"第N期"用"期"、综艺、主持等）
    · music       音乐：专辑、单曲、MV、演唱会/Live（FLAC/APE/DSD/24bit 等无损音频线索）
    · other       其余全部：普通真人电影/电视剧、软件/体育/游戏/电子书等
  只标出上述特殊题材，其它一律 other（是不是影视由 media_type 区分）。
  music 的 media_type 按结构判：纯音频专辑 → other，演唱会/MV 影像 → 按结构（通常 movie）。
  两轴正交：动漫剧场版 = movie + anime，别把题材塞进 media_type。

【零编造铁律】只能从给定字符串里**原样复制**子串，绝不允许翻译、补全缩写、
纠正拼写、把别名改成规范名、或凭常识补充原文没写的信息。某字段原文没明确写出
就留空——漏抽可接受，编造绝不可接受。你输出的每个值都必须能在 title 或
subtitle 里用「查找」原封不动定位到，否则该值会被判为无效丢弃。

注意：种子可能不是影视内容（软件、音乐专辑、游戏、电子书等）。非影视内容一律
所有字段给空值——教模型对这类输入保持沉默正是训练目的之一。
片名边界七铁律：
- **片名末尾的句号/点号不入片名**（。．.）——即使官方名含句号（"你的名字。"），
  检索用名不带；点号命名里中文贴写同理（"你的名字.Your.Name" 抽 "你的名字"）。
  注意：感叹号/问号是风格的一部分要保留（"Do It Yourself!!" 全抽）；
- **别名变体择干净**：同一作品多个别名中，含"剧场版/總集篇/总集篇/完结篇/特别篇/
  OVA"等类型标记词的变体**不标**，只标不含标记的干净别名（"进击的巨人剧场版：
  完结篇·最后的进击 / 进击的巨人：最后的进击" 只标后者）；若所有别名都含标记，
  标最短的那个；
- **章节小标题不入片名**："第X章/第X部/第X夜"及其后的小标题是分章信息
  （"鬼灭之刃：无限城篇 第一章 猗窝座再袭" 只抽 "鬼灭之刃：无限城篇"），
  章节表达抽入 episode；
- **版本描述词不入片名**：IMAX版/加长版/导演剪辑版/未删减版/修复版/重制版/3D版/
  杜比视界版/剧场版重映 等版本字样是发行属性不是名称——"星际穿越 IMAX版"只抽
  "星际穿越"（注意"剧场版"作为动漫电影类型词时同理不入片名，除非官方名就含它）；
- 发布标签词（官方/国语/中字/限转/禁转/独占/特效/应求/首发/DIY/粤语/双语/高清/杜比/
  活动置顶 等，常堆叠在副标题开头或片名两侧）**永远不是片名的一部分**：片名从真实
  名称的第一个字开始、最后一个字结束，一个标签字都不能带，也绝不能因剔除标签而
  误切片名本身的字；
- 合集资源（"XX执导电影合集"、"Collection"）：合集名称**整体**作为片名抽出，其中的
  人名是名称的一部分不要截断；电影合集 media_type=movie；
- 花絮/特典（EXTRAS/幕后花絮/特典/SP）：只抽正片名称（"星际穿越 幕后花絮"只抽
  "星际穿越"），描述词不入片名。

两个特殊规则：
- 成人影片（如 "IPZZ-034 ..."）视为影视：番号本身作为 title_en 抽出（如 "IPZZ-034"），
  有作品名也一并抽，演员名不抽；media_type=movie，content_type=other；
- 综艺的子栏目/特辑名（如 "训练日记"、"会员版"、"纯享版"）不是别名，不要抽入
  title_zh；只抽节目主名和"又名"类真正的别名。

只输出 JSON 数组，不要任何其他文字。每个元素形如：
{"id": "...", "title_en": [], "title_zh": [], "year": null, "season": [], "episode": [], "episode_total": [], "media_type": "movie", "content_type": "other"}

待标注数据：
"""

# 各字段在两个来源里的检索顺序（先命中主要来源，其余来源也全部标注）
FIELD_KEYS = ("title_en", "title_zh", "year", "season", "episode", "episode_total")
FIELD_LABEL = {
    "title_en": "TITLE_EN",
    "title_zh": "TITLE_ZH",
    "year": "YEAR",
    "season": "SEASON",
    "episode": "EPISODE",
    "episode_total": "EPISODE_TOTAL",
}


def find_codex() -> str:
    """定位 codex 二进制：先查 PATH，再兜底 homebrew node 的全局 npm bin。"""
    import glob
    import shutil

    path = shutil.which("codex")
    if path:
        return path
    for candidate in sorted(glob.glob("/opt/homebrew/Cellar/node/*/bin/codex"), reverse=True):
        return candidate
    raise RuntimeError("找不到 codex CLI，请先 npm install -g @openai/codex")


def call_claude(prompt: str, model: str | None) -> str:
    """调用 claude CLI 无头模式，返回原始文本输出。"""
    result = subprocess.run(
        ["claude", "-p", "--model", model or "claude-sonnet-5"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI 调用失败: {result.stderr.strip()[:500]}")
    return result.stdout


def call_codex(prompt: str, model: str | None) -> str:
    """调用 codex exec 无头模式。最终回复经 -o 文件取回（stdout 混有进度噪音）。
    不传 --model 时用 ~/.codex/config.toml 里的默认模型。"""
    import tempfile

    with tempfile.NamedTemporaryFile(mode="r", suffix=".txt", delete=False) as tmp:
        out_path = tmp.name
    cmd = [
        find_codex(),
        "exec",
        "--ephemeral",           # 标注调用不留会话记录
        "--skip-git-repo-check",
        "-s", "read-only",       # 纯文本任务，禁写盘
        "-o", out_path,
        "-",                     # 提示词走 stdin，避免参数长度限制
    ]
    if model:
        cmd[2:2] = ["--model", model]
    result = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=600)
    try:
        output = Path(out_path).read_text(encoding="utf-8")
    finally:
        Path(out_path).unlink(missing_ok=True)
    if result.returncode != 0 or not output.strip():
        raise RuntimeError(
            f"codex CLI 调用失败(exit={result.returncode}): {result.stderr.strip()[:500]}"
        )
    return output


ENGINES = {"claude": call_claude, "codex": call_codex}


def codex_default_model() -> str:
    """读 ~/.codex/config.toml 的 model 字段——只为溯源记录准确，读不到不碍事。"""
    try:
        text = (Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8")
        m = re.search(r'^model\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else "config-default"
    except OSError:
        return "config-default"


DEFAULT_MODEL = {"claude": "claude-sonnet-5", "codex": codex_default_model()}


def parse_json_array(text: str) -> list[dict]:
    """从模型输出中提取 JSON 数组（容忍 ```json 围栏）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"输出中找不到 JSON 数组: {text[:200]}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("输出不是 JSON 数组")
    return data


def find_all(haystack: str, needle: str, word_boundary: bool = False) -> list[tuple[int, int]]:
    """返回 needle 在 haystack 中所有出现位置。

    word_boundary=True 时要求命中处两侧不是 ASCII 字母数字——短英文片名
    （It / Up / Her）不加这个守卫会无声命中 "Criterion" 之类单词内部。
    """
    hits, pos = [], 0
    while True:
        i = haystack.find(needle, pos)
        if i == -1:
            return hits
        end = i + len(needle)
        if word_boundary:
            # 只在 needle 端点本身是字母数字的一侧查边界："Ba Ba Ba!" 以标点
            # 结尾，右侧紧贴年份（"Ba Ba Ba!2022"）不算词内命中
            before = haystack[i - 1] if i > 0 else ""
            after = haystack[end] if end < len(haystack) else ""
            head_alnum = needle[0].isascii() and needle[0].isalnum()
            tail_alnum = needle[-1].isascii() and needle[-1].isalnum()
            if (head_alnum and before.isascii() and before.isalnum()) or (
                tail_alnum and after.isascii() and after.isalnum()
            ):
                pos = i + 1
                continue
        hits.append((i, end))
        pos = i + 1


def resolve_media_type(value: object, spans: list[dict]) -> str:
    """把模型给的 media_type 归一到 MEDIA_TYPES；非法/缺失时按 span 兜底。

    兜底规则（仅在模型没给合法值时触发，正常极少走到）：有季/集类 span → series，
    完全无 span（非影视负样本）→ other，其余 → movie。"""
    if isinstance(value, str) and value.strip().lower() in MEDIA_TYPES:
        return value.strip().lower()
    episodic = any(s["field"] in ("SEASON", "EPISODE", "EPISODE_TOTAL") for s in spans)
    if episodic:
        return "series"
    return "movie" if spans else "other"


def resolve_content_type(value: object, media_type: str) -> str:
    """把模型给的 content_type 归一到 CONTENT_TYPES；非法/缺失时兜底。

    兜底（仅在模型没给合法值时）：一律归 other——它本就是"动漫/纪录片/综艺
    之外全部"的残差项，缺失时归此最安全（media_type 参数保留以备将来细分）。"""
    _ = media_type
    if isinstance(value, str) and value.strip().lower() in CONTENT_TYPES:
        return value.strip().lower()
    return "other"


def locate_spans(sample: dict, extraction: dict) -> tuple[list[dict], list[str]]:
    """把模型抽出的子串定位成字符 span；返回 (spans, 待复核原因列表)。"""
    spans: list[dict] = []
    review: list[str] = []
    sources = {"title": sample["title"], "subtitle": sample.get("subtitle", "")}

    for key in FIELD_KEYS:
        raw = extraction.get(key)
        values = raw if isinstance(raw, list) else ([raw] if raw else [])
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            # 词边界守卫只对英文片名开：season/episode 常紧贴字母数字
            # （"S01E10"），year 由提示词规则兜底，中文没有词边界概念
            boundary = key == "title_en" and value.isascii()
            found = False
            for source, text in sources.items():
                for start, end in find_all(text, value, word_boundary=boundary):
                    spans.append(
                        {"source": source, "field": FIELD_LABEL[key], "start": start, "end": end}
                    )
                    found = True
            if not found:
                review.append(f"{key} 子串未在原文找到: {value!r}")

    # 重叠消解按来源分开做（title 和 subtitle 的坐标系独立）
    resolved = []
    for source in sources:
        resolved.extend(
            {**s, "source": source}
            for s in resolve_overlaps([s for s in spans if s["source"] == source])
        )
    return resolved, review


def load_done_ids(path: Path) -> set[str]:
    """读取已完成 id。发现残行（进程被杀时写了半行）就地修复：
    好行原样保留、坏行剔除并原子重写，被剔除的样本会自动重标。"""
    if not path.exists():
        return set()
    good_lines, ids, bad = [], set(), 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                ids.add(json.loads(stripped)["id"])
                good_lines.append(stripped)
            except (json.JSONDecodeError, KeyError):
                bad += 1
    if bad:
        tmp = path.with_suffix(".repair.tmp")
        tmp.write_text("".join(line + "\n" for line in good_lines), encoding="utf-8")
        os.replace(tmp, path)
        print(f"检测到 {bad} 个残行（上次中断所致），已修复文件，对应样本将重标")
    return ids


def acquire_lock(out_path: Path) -> None:
    """输出文件级进程锁：防止两个标注进程同时追加同一文件互相踩踏。
    锁文件记录 pid；持有进程已不存在则视为陈锁自动接管。"""
    lock = out_path.with_suffix(out_path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            atexit.register(lambda: lock.unlink(missing_ok=True))
            return
        except FileExistsError:
            try:
                pid = int(lock.read_text().strip() or "0")
                os.kill(pid, 0)  # 探活，不发信号
            except (ValueError, ProcessLookupError, FileNotFoundError):
                lock.unlink(missing_ok=True)  # 陈锁，清掉重试
                continue
            except PermissionError:
                pass  # 进程存在但无权限探测，按存活处理
            sys.exit(f"已有标注进程（pid={pid}）在写 {out_path}，同一输出文件只允许一个进程")


def main() -> None:
    parser = argparse.ArgumentParser(description="大模型蒸馏标注")
    parser.add_argument("--in", dest="input", default="ml/data/raw/samples.jsonl")
    parser.add_argument("--out", default="ml/data/labeled/annotations.jsonl")
    parser.add_argument("--batch", type=int, default=10, help="每次 CLI 调用标注的样本数")
    parser.add_argument("--limit", type=int, default=0, help="本次最多标注多少条（0=不限）")
    parser.add_argument("--engine", choices=sorted(ENGINES), default="claude", help="标注引擎")
    parser.add_argument("--model", default=None, help="模型覆写（默认: claude→sonnet-5, codex→config.toml）")
    parser.add_argument("--max-consecutive-failures", type=int, default=3,
                        help="连续失败多少个批次后熔断退出（认证过期/额度耗尽时快速止损）")
    parser.add_argument("--workers", type=int, default=1,
                        help="并行批次数（codex 建议 4；claude 订阅额度紧建议保持 1）")
    args = parser.parse_args()
    call_model = ENGINES[args.engine]
    annotator = f"{args.engine}:{args.model or DEFAULT_MODEL[args.engine]}"

    out_path = Path(args.out)
    acquire_lock(out_path)
    samples = read_jsonl(args.input)
    done = load_done_ids(out_path)
    todo = [s for s in samples if s["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    batches = [todo[i : i + args.batch] for i in range(0, len(todo), args.batch)]
    print(f"待标注 {len(todo)} 条（已完成 {len(done)} 条），标注器 {annotator}，"
          f"规范 v{PROMPT_VERSION}，并行 {args.workers}")

    # 并行共享状态：写锁保证 JSONL 行不互相穿插；熔断计数在并行下是
    # "近期连续失败"的近似（成功即清零），语义与串行一致
    write_lock = threading.Lock()
    stop = threading.Event()
    state = {"review": 0, "done": 0, "fail_streak": 0}

    def process_batch(batch: list[dict]) -> None:
        if stop.is_set():
            return
        payload = json.dumps(
            [{"id": s["id"], "title": s["title"], "subtitle": s.get("subtitle", "")} for s in batch],
            ensure_ascii=False,
            indent=1,
        )
        extractions = None
        for attempt in (1, 2):  # 每批次内重试一次（模型输出偶发不合法 JSON）
            try:
                extractions = parse_json_array(call_model(PROMPT_HEADER + payload, args.model))
                break
            except (RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
                print(f"批次(首样本 {batch[0]['id']}) 第 {attempt} 次尝试失败: {str(exc)[:300]}")
        with write_lock:
            if extractions is None:
                state["fail_streak"] += 1
                if state["fail_streak"] >= args.max_consecutive_failures:
                    stop.set()
                return
            state["fail_streak"] = 0

            by_id = {e.get("id"): e for e in extractions if isinstance(e, dict)}
            results = []
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for sample in batch:
                extraction = by_id.get(sample["id"])
                if extraction is None:
                    print(f"样本 {sample['id']} 无返回，跳过（重跑时自动补）")
                    continue
                spans, review = locate_spans(sample, extraction)
                media_type = resolve_media_type(extraction.get("media_type"), spans)
                record = {
                    **sample,
                    "spans": spans,
                    "media_type": media_type,
                    "content_type": resolve_content_type(extraction.get("content_type"), media_type),
                    "annotator": annotator,
                    "prompt_version": PROMPT_VERSION,
                    "annotated_at": now,
                }
                if review:
                    record["review"] = review
                    state["review"] += 1
                results.append(record)
            append_jsonl(out_path, results)
            state["done"] += len(batch)
            print(f"进度 {state['done']}/{len(todo)}")

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(process_batch, b) for b in batches]
        for future in as_completed(futures):
            future.result()  # 让线程内的意外异常浮出来而不是被吞掉

    if stop.is_set():
        sys.exit(
            f"连续 {args.max_consecutive_failures} 个批次失败，熔断退出。"
            "请检查 CLI 登录态/额度后重跑（已完成部分自动续接）"
        )
    print(f"完成。待人工复核 {state['review']} 条（grep '\"review\"' {args.out}）")


if __name__ == "__main__":
    main()
