"""扫描器专用的 TMDB 收敛验证器——"匹配不是搜索问题，是验证问题"。

与豆瓣入口的 ``resolve_douban_to_tmdb`` 不同：豆瓣入口的查询词是用户输入的
真实片名，"搜索结果唯一即命中"够用；扫描器的查询词来自任意文件/目录名，
**唯一 ≠ 正确**——噪声词也可能唯一命中一个错误条目（实测案例：MV「心墙
2019」被唯一搜索结果错挂到 1965 年老片《心墙魅影》）。

因此这里改为两段式判定，任何候选被采信都必须过门槛且无本地证据反对：

  ① 标题门槛（必过）：查询词与候选的主名/原名/别名（含 TMDB alternative
     titles）经标点归一后精确相等；不过门槛的候选直接淘汰。归一化会去掉
     点号/冒号/撇号等（点分命名 ``13.Reasons.Why``、TMDB 副标题冒号
     ``FBI: International`` 都因此不再误伤）。
  ② 本地证据校验（对过门槛者逐一验证）：
     - 年份：电影名里的年份基本就是影片年份，偏差 ≥2 年视为**反证**淘汰；
       剧集季包的年份常是资源发布年而非首播年（如 Breaking Bad S03 2011），
       只作佐证、不作反证。
     - 时长（电影）：实测时长 × TMDB runtime，±3 分钟强佐证 / ±10 分钟弱
       佐证；不吻合不淘汰——导演剪辑版/加长版的偏差是正常的。
     - 季数（剧集）：本地看到 SNN 而候选总季数 < NN 视为**反证**淘汰
       （衍生/花絮条目通常死在这一刀）；≥ NN 则是佐证。
       **动画豁免**：TMDB 对日漫/国产年番普遍把多期合并为单季连续编号
       （实测：《租借女友》5 期在 TMDB 是 1 季 60 集、《斗罗大陆Ⅱ》是
       1 季 182 集在播），PT 站的 SNN 惯例与之系统性冲突——动画类候选
       且年份间隔合理（本地年份 − 候选首播年 ≤ 本地季号×2）时季数不作
       反证。年份间隔这一刀挡住"错代同名老条目"（Berserk 2016 种子不会
       因豁免错挂 1997 版：间隔 19 年远超 S02×2）。
     - 集数（剧集）：对应季的集数 ≥ 本地集号作佐证；不足不淘汰——TMDB 对
       在播季的集数经常滞后。
     - 副标题（经下载器落地的文件才有，见 download_hint 表）：其中的中文
       片名作**备选查询词**（主词全灭后换词重跑）；「全N集」与对应季
       episode_count 精确相等作佐证。

最终裁决（保守优先，绝不静默错挂）：
  过门槛且未被反证的候选恰好一个 → 命中；多个时依次用"有佐证者唯一"、
  "年份精确相等者唯一"、"时长强吻合者唯一（电影）"收窄；仍不唯一 → 放弃，
  进待识别清单人工认领。例外兜底：门槛全灭的电影允许"时长强吻合且无反证
  者唯一"直接实锤——enrich 对短中文名偶发截断，物理时长此时是唯一强证据。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace

from movieclaw_media.models import MediaKind
from movieclaw_media.tmdb import TmdbClient

logger = logging.getLogger("movieclaw_api.library_resolve")

# 逐候选拉详情的上限（限流友好）；时长佐证的分级容差（秒）
_DETAIL_CANDIDATES_MAX = 5
_RUNTIME_STRONG_SECONDS = 180
_RUNTIME_WEAK_SECONDS = 600

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)


def normalize_title(text: str) -> str:
    """标题归一形态：casefold 后去掉全部标点/空白，只留字母数字与汉字。"""
    return _NON_WORD.sub("", text).replace("_", "").casefold()


@dataclass
class LocalEvidence:
    """从本地文件/目录名（及下载线索副标题）收集到的全部识别证据。"""

    title: str
    year: int | None = None
    season: int | None = None  # 本地看到的最大季号（S00 特别篇不算）
    episode: int | None = None  # 本地看到的最大集号
    duration_seconds: int | None = None  # ffprobe 实测时长（电影消歧用）
    # 备选查询词：种子副标题里的中文片名（拼音/意译名种子仅靠目录名在
    # TMDB 无解，如「Qiang Qiang Shi Yi」实为《锵锵拾遗》）；主查询词
    # 全灭后用它重跑一轮完整验证
    alt_title: str | None = None
    # 副标题「全N集」声明的总集数（剧集）：与 TMDB 对应季 episode_count
    # 精确相等作佐证
    total_episodes: int | None = None


# 副标题里的总集数声明：「全32集」「全 13 集」
_TOTAL_EPISODES = re.compile(r"全\s*(\d{1,4})\s*集")


def parse_total_episodes(subtitle: str) -> int | None:
    """从副标题里解析「全N集」声明；没有返回 None。"""
    match = _TOTAL_EPISODES.search(subtitle)
    return int(match.group(1)) if match else None


@dataclass
class _Candidate:
    """一个 TMDB 搜索候选 + 详情补充的验证字段。"""

    tmdb_id: int
    title: str
    original_title: str
    year: int | None
    names: set[str] = field(default_factory=set)  # 归一化后的全部名称
    runtime_seconds: int | None = None
    season_count: int | None = None
    episode_counts: dict[int, int] = field(default_factory=dict)  # 季号 → 集数
    is_animation: bool = False  # 动画类（TMDB genre 16）：季数反证豁免用


async def verify_resolve(
    client: TmdbClient,
    kind: MediaKind,
    evidence: LocalEvidence,
    *,
    language: str = "zh-CN",
) -> int | None:
    """按本地证据验证 TMDB 搜索候选；唯一可信者返回其 tmdb_id，否则 None。

    主查询词（文件/目录名）全灭且有备选查询词（副标题中文名）时，换词
    重跑一轮完整验证——标题门槛/反证/裁决对备选词同样生效，不降标准。
    """
    picked = await _verify_query(client, kind, evidence, language)
    if picked is not None or not evidence.alt_title:
        return picked
    if normalize_title(evidence.alt_title) == normalize_title(evidence.title):
        return None  # 备选词与主词同形，重跑是浪费
    logger.info("扫描收敛换词重试：「%s」→ 副标题中文名「%s」", evidence.title, evidence.alt_title)
    return await _verify_query(
        client, kind, replace(evidence, title=evidence.alt_title, alt_title=None), language
    )


async def _verify_query(
    client: TmdbClient,
    kind: MediaKind,
    evidence: LocalEvidence,
    language: str,
) -> int | None:
    """单个查询词的完整验证：普通搜索 → 年份补搜。"""
    # 点分命名的查询词（Shark.Tank）会拖垮 TMDB 搜索召回，先归一成空格
    query = re.sub(r"[._]+", " ", evidence.title).strip()
    candidates = await _search_candidates(client, kind, query, language)
    picked = await _verify_pool(client, kind, evidence, candidates, language)
    if picked is not None or evidence.year is None:
        return picked
    # 年份补搜：普通搜索按热度排序，冷门正主可能排在前 5 之外（实测案例：
    # 「Berserk 2016」的 2016 版剑风传奇）——带年份参数再捞一轮新候选
    extra = await _search_candidates(client, kind, query, language, year=evidence.year)
    known = {c.tmdb_id for c in candidates}
    fresh = [c for c in extra if c.tmdb_id not in known]
    if not fresh:
        return None
    logger.info(
        "扫描收敛年份补搜：「%s」(%s) 新增 %d 个候选", evidence.title, evidence.year, len(fresh)
    )
    picked_id = await _verify_pool(client, kind, evidence, fresh, language)
    if picked_id is None:
        return None
    # 补搜池本身按年份过滤而来，"年份相同"在这里是循环论证、零信息量；
    # 必须另有非平凡佐证才可采信（实测反例：「Never Give Up」剧集种子
    # 差点错挂到同年同英文别名的电影《龍昇不打烊》）
    picked = next(c for c in fresh if c.tmdb_id == picked_id)
    strong = _strong_corroborations(kind, evidence, picked)
    if not strong:
        logger.info(
            "扫描收敛年份补搜否决：《%s》除年份外无有效佐证，进待识别（查询「%s」）",
            picked.title,
            evidence.title,
        )
        return None
    return picked_id


async def _search_candidates(
    client: TmdbClient, kind: MediaKind, query: str, language: str, *, year: int | None = None
) -> list[_Candidate]:
    params: dict = {"query": query, "language": language}
    if year is not None:
        # 电影和剧集的年份过滤参数名不同——TMDB 的历史包袱
        key = "primary_release_year" if kind is MediaKind.MOVIE else "first_air_date_year"
        params[key] = year
    data = await client.get(f"search/{kind.value}", params)
    candidates = [_from_search(raw) for raw in data.get("results", [])]
    return [c for c in candidates if c is not None][:_DETAIL_CANDIDATES_MAX]


async def _verify_pool(
    client: TmdbClient,
    kind: MediaKind,
    evidence: LocalEvidence,
    candidates: list[_Candidate],
    language: str,
) -> int | None:
    if not candidates:
        return None
    wanted = normalize_title(evidence.title)
    for candidate in candidates:
        await _load_detail(client, kind, candidate, language)

    # ① 标题门槛
    passers = [c for c in candidates if wanted in c.names]
    if not passers:
        # 电影兜底：enrich 对短中文名偶发截断（如「两生花」→「两生」），标题
        # 门槛必然不过，但 TMDB 模糊搜索仍能召回正主——此时唯一"时长强吻合
        # 且无反证"的候选可直接实锤（时长是文件识别独有的物理证据）
        picked = _runtime_rescue(kind, evidence, candidates)
        if picked is not None:
            logger.info(
                "扫描收敛时长消歧：「%s」→《%s》(%s, tmdb=%d)",
                evidence.title,
                picked.title,
                picked.year,
                picked.tmdb_id,
            )
            return picked.tmdb_id
        logger.info(
            "扫描收敛放弃：「%s」的 %d 个候选均未过标题门槛（首位：《%s》）",
            evidence.title,
            len(candidates),
            candidates[0].title,
        )
        return None

    # ② 反证淘汰
    survivors = []
    for c in passers:
        counter = _counter_evidence(kind, evidence, c)
        if counter:
            logger.info(
                "扫描收敛淘汰候选《%s》：%s（查询「%s」）", c.title, counter, evidence.title
            )
            continue
        survivors.append(c)
    if not survivors:
        return None

    # ③ 裁决：唯一幸存 → 逐级收窄 → 歧义放弃
    picked = _adjudicate(kind, evidence, survivors)
    if picked is None:
        logger.info(
            "扫描收敛歧义：「%s」有 %d 个可信候选（%s），进待识别",
            evidence.title,
            len(survivors),
            "、".join(f"《{c.title}》({c.year})" for c in survivors[:3]),
        )
        return None
    reasons = _corroborations(kind, evidence, picked) or ["唯一过标题门槛"]
    logger.info(
        "扫描收敛命中：「%s」→《%s》(%s, tmdb=%d)，佐证：%s",
        evidence.title,
        picked.title,
        picked.year,
        picked.tmdb_id,
        "、".join(reasons),
    )
    return picked.tmdb_id


def _adjudicate(
    kind: MediaKind, evidence: LocalEvidence, survivors: list[_Candidate]
) -> _Candidate | None:
    if len(survivors) == 1:
        return survivors[0]
    corroborated = [c for c in survivors if _corroborations(kind, evidence, c)]
    if len(corroborated) == 1:
        return corroborated[0]
    pool = corroborated or survivors
    if evidence.year is not None:
        exact_year = [c for c in pool if c.year == evidence.year]
        if len(exact_year) == 1:
            return exact_year[0]
    if kind is MediaKind.MOVIE and evidence.duration_seconds:
        strong = [
            c
            for c in pool
            if c.runtime_seconds
            and abs(c.runtime_seconds - evidence.duration_seconds) <= _RUNTIME_STRONG_SECONDS
        ]
        if len(strong) == 1:
            return strong[0]
    return None


def _runtime_rescue(
    kind: MediaKind, evidence: LocalEvidence, candidates: list[_Candidate]
) -> _Candidate | None:
    """标题门槛全灭时的电影兜底：时长强吻合且无反证的候选唯一 → 采信。"""
    if kind is not MediaKind.MOVIE or not evidence.duration_seconds:
        return None
    hits = [
        c
        for c in candidates
        if c.runtime_seconds
        and abs(c.runtime_seconds - evidence.duration_seconds) <= _RUNTIME_STRONG_SECONDS
        and _counter_evidence(kind, evidence, c) is None
    ]
    return hits[0] if len(hits) == 1 else None


def _counter_evidence(kind: MediaKind, evidence: LocalEvidence, c: _Candidate) -> str | None:
    """返回淘汰理由；None 表示无反证。"""
    if (
        kind is MediaKind.MOVIE
        and evidence.year is not None
        and c.year is not None
        and abs(c.year - evidence.year) >= 2
    ):
        return f"年份反证（本地 {evidence.year} vs 候选 {c.year}）"
    if (
        kind is MediaKind.TV
        and evidence.season
        and c.season_count is not None
        and c.season_count < evidence.season
        and not _anime_season_exempt(evidence, c)
    ):
        return f"季数反证（本地 S{evidence.season:02d} vs 候选共 {c.season_count} 季）"
    return None


def _anime_season_exempt(evidence: LocalEvidence, c: _Candidate) -> bool:
    """动画类候选的季数反证豁免（见模块头注释「动画豁免」）。

    豁免条件：候选是动画，且年份不构成"错代"矛盾——本地或候选缺年份时
    放行；两边都有年份时要求 0 ≤ 本地年份 − 候选首播年 ≤ 本地季号×2
    （连续年番第 N 季大约在首播后 N 年内播出，远超此间隔的是同名老条目）。
    """
    if not c.is_animation:
        return False
    if evidence.year is None or c.year is None:
        return True
    assert evidence.season is not None
    return 0 <= evidence.year - c.year <= evidence.season * 2


def _corroborations(kind: MediaKind, evidence: LocalEvidence, c: _Candidate) -> list[str]:
    """收集本地证据对该候选的全部佐证（供裁决与日志）。"""
    reasons: list[str] = []
    if evidence.year is not None and c.year is not None and abs(c.year - evidence.year) <= 1:
        reasons.append("年份相同" if c.year == evidence.year else "年份±1")
    if kind is MediaKind.MOVIE and evidence.duration_seconds and c.runtime_seconds:
        gap = abs(c.runtime_seconds - evidence.duration_seconds)
        if gap <= _RUNTIME_STRONG_SECONDS:
            reasons.append("时长吻合")
        elif gap <= _RUNTIME_WEAK_SECONDS:
            reasons.append("时长接近")
    if kind is MediaKind.TV:
        if evidence.season and c.season_count is not None and c.season_count >= evidence.season:
            reasons.append(f"季数≥{evidence.season}")
        if evidence.season and evidence.episode:
            count = c.episode_counts.get(evidence.season)
            if count is not None and count >= evidence.episode:
                reasons.append("集数覆盖")
        if _total_episodes_match(evidence, c):
            reasons.append(f"全{evidence.total_episodes}集吻合")
    return reasons


def _total_episodes_match(evidence: LocalEvidence, c: _Candidate) -> bool:
    """副标题「全N集」与 TMDB 对应季集数**精确相等**（无季号按第 1 季）。

    只作佐证不作反证：在播季 TMDB 集数常滞后，且平台切割集数不一致。"""
    if not evidence.total_episodes:
        return False
    return c.episode_counts.get(evidence.season or 1) == evidence.total_episodes


def _strong_corroborations(kind: MediaKind, evidence: LocalEvidence, c: _Candidate) -> list[str]:
    """年份之外的**非平凡**佐证：时长吻合、季数（本地 S≥2 才有区分度——
    任何剧都至少 1 季）、集数覆盖（本地 E≥2 才有区分度）。补搜采信专用。"""
    reasons: list[str] = []
    if kind is MediaKind.MOVIE and evidence.duration_seconds and c.runtime_seconds:
        if abs(c.runtime_seconds - evidence.duration_seconds) <= _RUNTIME_WEAK_SECONDS:
            reasons.append("时长")
    if kind is MediaKind.TV:
        if (
            evidence.season
            and evidence.season >= 2
            and c.season_count is not None
            and c.season_count >= evidence.season
        ):
            reasons.append(f"季数≥{evidence.season}")
        if evidence.season and evidence.episode and evidence.episode >= 2:
            count = c.episode_counts.get(evidence.season)
            if count is not None and count >= evidence.episode:
                reasons.append("集数覆盖")
        if _total_episodes_match(evidence, c):
            reasons.append(f"全{evidence.total_episodes}集吻合")
    return reasons


def _from_search(raw: dict) -> _Candidate | None:
    tmdb_id = raw.get("id")
    title = raw.get("title") or raw.get("name") or ""
    if not tmdb_id or not title:
        return None
    original = raw.get("original_title") or raw.get("original_name") or title
    candidate = _Candidate(
        tmdb_id=tmdb_id,
        title=title,
        original_title=original,
        year=_parse_year(raw.get("release_date") or raw.get("first_air_date") or ""),
    )
    candidate.names = {normalize_title(title), normalize_title(original)}
    return candidate


async def _load_detail(
    client: TmdbClient, kind: MediaKind, candidate: _Candidate, language: str
) -> None:
    """拉候选详情补充验证字段；单候选失败按"信息缺失"处理，不中断整体。"""
    try:
        detail = await client.get(
            f"{kind.value}/{candidate.tmdb_id}",
            {"append_to_response": "alternative_titles,translations", "language": language},
        )
    except Exception as exc:  # noqa: BLE001 -- TMDB 波动只降级该候选
        logger.debug("候选详情获取失败（tmdb=%s）：%s", candidate.tmdb_id, exc)
        return
    alt = detail.get("alternative_titles") or {}
    # 电影的别名在 titles，剧集在 results——TMDB 的历史包袱
    for entry in alt.get("titles") or alt.get("results") or []:
        name = entry.get("title")
        if name:
            candidate.names.add(normalize_title(name))
    # 外语片的英文名往往只存在 translations（如《自由的幻影》的英文名），
    # alternative_titles 反而没有——两处都收，标题门槛才不冤枉外语片
    translations = (detail.get("translations") or {}).get("translations") or []
    for entry in translations:
        data = entry.get("data") or {}
        name = data.get("title") or data.get("name")
        if name:
            candidate.names.add(normalize_title(name))
    if kind is MediaKind.MOVIE:
        runtime = detail.get("runtime")
        if runtime:
            candidate.runtime_seconds = int(runtime) * 60
    else:
        seasons = detail.get("number_of_seasons")
        if seasons:
            candidate.season_count = int(seasons)
        # genre 16 = Animation（id 跨语言稳定，名称会随 language 变化）
        candidate.is_animation = any(
            g.get("id") == 16 for g in detail.get("genres") or []
        )
        for season in detail.get("seasons") or []:
            number, count = season.get("season_number"), season.get("episode_count")
            if number and count:
                candidate.episode_counts[int(number)] = int(count)


def _parse_year(date_str: str) -> int | None:
    if len(date_str) >= 4 and date_str[:4].isdigit():
        return int(date_str[:4])
    return None
