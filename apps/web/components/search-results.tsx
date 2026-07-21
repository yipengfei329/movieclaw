"use client";

import { useEffect, useMemo, useState } from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { LayersIcon, ListIcon, PhotoIcon } from "@/components/icons";
import { PosterImage } from "@/components/poster-image";
import { Tooltip } from "@/components/tooltip";
import type { SearchScope } from "@/lib/categories";
import {
  fetchSearchSnapshot,
  streamSearchTorrents,
  type SiteSearchStatus,
  type TorrentAttrs,
  type TorrentHit,
} from "@/lib/api/search";
import { submitTorrentDownload } from "@/lib/api/downloaders";
import { defaultLibraryFor } from "@/lib/api/libraries";
import { cachedImageUrl } from "@/lib/image-proxy";
import { formatDateTime, formatRelativeTime } from "@/lib/time";

/**
 * 搜索结果页（主内容区）——消费 SSE 流式搜索，结果渐进渲染。
 *
 * 数据流：streamSearchTorrents 逐事件回调，快的站点先出结果先上屏，不被最慢的
 * 站点拖住。页头状态行右侧有站点状态聚合 chip（点开看逐站命中/耗时/失败原因），
 * 流式过程中的等待感由页头进度条与骨架屏承载。
 *
 * 筛选区采用「频率分层」的三层结构（Airbnb 式抽屉方案）：
 * 1. **常驻工具栏**：影视类型分段切换（视图态，不是普通筛选）+ 分辨率 chips
 *    （唯一高频筛选维度）+ 带角标的「筛选」按钮 + 右对齐的排序控件；
 * 2. **筛选弹层**：站点/年份/季/集/片源/编码/HDR/音频/压制组按组分区，
 *    chips 多选（组内=或，组间=且），底部实时显示命中数；
 * 3. **已应用条件回显行**：弹层里激活的条件以可摘除 chip 回显在结果上方，
 *    用户不用打开弹层就知道列表被什么约束着。
 *
 * 筛选与排序都是纯前端操作，不重新发起搜索；新搜索重置筛选、保留排序偏好。
 * 流式期间新到的结果实时并入当前筛选/排序视图。
 */
export interface SearchQuery {
  keyword: string;
  /** 搜索范围（标签换算而来）：展示名 + 分类/站点组合 */
  scope: SearchScope;
  /**
   * 快照预览：非空时不发起实时搜索，改为加载该条历史留存的结果快照
   * （页顶提示条标注快照年龄，可一键切回实时搜索）。
   */
  snapshotId?: number;
}

export interface SearchResultsProps {
  query: SearchQuery;
  /** 发起实时搜索（快照提示条的「重新搜索」按钮）；不传则不渲染该按钮。 */
  onResearch?: (keyword: string, scope: SearchScope) => void;
}

/**
 * 整页搜索阶段：connecting=流尚未建立（start 事件未到），streaming=站点结果陆续
 * 到达中，done=全部站点已返回，error=流本身失败（网络/鉴权——单站失败不算）。
 */
type Phase = "connecting" | "streaming" | "done" | "error";

/** 单个站点的实时进度（由 start / site_result / site_error 事件逐步填充）。 */
interface SiteProgress {
  site_id: string;
  site_name: string;
  phase: "searching" | "ok" | "error";
  count: number;
  error: string | null;
  elapsed_ms: number | null;
}

/** 耗时展示：统一到秒（一位小数），站点状态弹层用。 */
function formatElapsed(ms: number): string {
  return `${(ms / 1000).toFixed(1)} 秒`;
}

/** 影视类型的展示文案（值与后端 TorrentAttrs.media_type 一致）。 */
const MEDIA_TYPE_LABEL: Record<string, string> = {
  movie: "电影",
  tv: "剧集",
};

/* —— 排序 —— */

type SortKey = "seeders" | "time" | "size" | "snatched";

const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "seeders", label: "做种数" },
  { key: "time", label: "发布时间" },
  { key: "size", label: "体积" },
  { key: "snatched", label: "完成数" },
];

interface SortState {
  key: SortKey;
  dir: "desc" | "asc";
}

/** 从地址栏的 sort=key:dir 参数还原排序态，非法/缺失时回落到默认（做种数降序）。 */
function readSortFromUrl(): SortState {
  const fallback: SortState = { key: "seeders", dir: "desc" };
  if (typeof window === "undefined") return fallback;
  const [key, dir] = (new URLSearchParams(window.location.search).get("sort") ?? "").split(":");
  if (SORT_OPTIONS.some((o) => o.key === key) && (dir === "asc" || dir === "desc")) {
    return { key: key as SortKey, dir };
  }
  return fallback;
}

/** 结果展示视图：group=按作品分组（默认），list=平铺种子列表，poster=图览。 */
type ResultView = "group" | "list" | "poster";

/**
 * 视图初值：图览预设/地址栏 poster 参数优先，其次地址栏 view=list（平铺），
 * 默认分组。与 readSortFromUrl 同理，本组件仅在客户端渲染，可安全读 window。
 */
function initialView(posterPreset: boolean): ResultView {
  if (posterPreset) return "poster";
  if (
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).get("view") === "list"
  ) {
    return "list";
  }
  return "group";
}

function sortValue(hit: TorrentHit, key: SortKey): number {
  switch (key) {
    case "time":
      // 无发布时间的排最后（升降序都垫底更符合直觉，用 -Infinity 简化处理）
      return hit.upload_time ? Date.parse(hit.upload_time) : -Infinity;
    case "size":
      return hit.size_bytes;
    case "snatched":
      return hit.snatched;
    default:
      return hit.seeders;
  }
}

/* —— 筛选状态 —— */

interface Filters {
  resolution: Set<string>; // 常驻 chips
  // 以下维度收在筛选弹层里，全部多选（组内=或）
  site: Set<string>;
  year: Set<number>;
  season: Set<number>;
  episode: Set<number>;
  source: Set<string>; // media_source 值 + 特殊值 "Remux"
  codec: Set<string>;
  hdr: Set<string>;
  audio: Set<string>;
  group: Set<string>;
}

function emptyFilters(): Filters {
  return {
    resolution: new Set(),
    site: new Set(),
    year: new Set(),
    season: new Set(),
    episode: new Set(),
    source: new Set(),
    codec: new Set(),
    hdr: new Set(),
    audio: new Set(),
    group: new Set(),
  };
}

/** 弹层内维度的键（回显行与角标计数只统计这些——类型/分辨率在工具栏上自明）。 */
const SHEET_KEYS = [
  "site", "year", "season", "episode", "source", "codec", "hdr", "audio", "group",
] as const;
type SheetKey = (typeof SHEET_KEYS)[number];

function sheetSelectionCount(f: Filters): number {
  return SHEET_KEYS.reduce((sum, key) => sum + f[key].size, 0);
}

function hasActiveFilters(f: Filters): boolean {
  return f.resolution.size > 0 || sheetSelectionCount(f) > 0;
}

/** 筛选维度标识：与 Filters 的键一一对应（分面计数按维度做自排除时引用）。 */
type FilterDim = keyof Filters;

/** 作品分组里「未识别」桶的聚合键（解析不出片名的种子都归这里，按原始名平铺）。 */
const ENTITY_UNPARSED = "__unparsed__";

/**
 * 作品聚合键：解析主名（中文优先）定作品；电影按年份区分（沙丘 1984 vs 2021），
 * 剧集不按年拆（同一部剧的各季跨年）。别名归并（沙丘2 / Dune: Part Two 各自成组）
 * 留给 TMDB 回填后用 tmdb_id 解决。
 */
function entityKeyOf(hit: TorrentHit): string {
  const title = hit.attrs?.titles_zh?.[0] ?? hit.attrs?.titles_en?.[0];
  if (!title) return ENTITY_UNPARSED;
  const type = hit.attrs?.media_type ?? "?";
  if (type === "tv") return `tv|${title}`;
  return `${type}|${title}|${hit.attrs?.year ?? "?"}`;
}

/**
 * 逐维度的通过判定（维度未激活即通过）。过滤与分面计数共用这一份逻辑：
 * matchesFilters 要求全部维度通过；计数时则需要知道某条结果具体挂在哪个维度上
 * （见 aggregateFacets 的自排除计数），拆成表驱动两边才不会出现口径不一致。
 */
const FILTER_DIMENSIONS: { dim: FilterDim; pass: (hit: TorrentHit, f: Filters) => boolean }[] = [
  {
    dim: "resolution",
    pass: (hit, f) =>
      !f.resolution.size || (!!hit.attrs?.resolution && f.resolution.has(hit.attrs.resolution)),
  },
  { dim: "site", pass: (hit, f) => !f.site.size || f.site.has(hit.site_id) },
  {
    dim: "year",
    pass: (hit, f) => !f.year.size || (hit.attrs?.year != null && f.year.has(hit.attrs.year)),
  },
  {
    dim: "season",
    pass: (hit, f) => !f.season.size || (hit.attrs?.seasons ?? []).some((s) => f.season.has(s)),
  },
  {
    dim: "episode",
    // 全集包（complete）视为包含任意一集——它没逐集列出但语义上都有
    pass: (hit, f) =>
      !f.episode.size ||
      (hit.attrs?.episodes ?? []).some((e) => f.episode.has(e)) ||
      hit.attrs?.complete === true,
  },
  {
    dim: "source",
    pass: (hit, f) => {
      if (!f.source.size) return true;
      const a = hit.attrs;
      const bySource = a?.media_source ? f.source.has(a.media_source) : false;
      const byRemux = f.source.has("Remux") && a?.remux === true;
      return bySource || byRemux;
    },
  },
  {
    dim: "codec",
    pass: (hit, f) =>
      !f.codec.size || (!!hit.attrs?.video_codec && f.codec.has(hit.attrs.video_codec)),
  },
  { dim: "hdr", pass: (hit, f) => !f.hdr.size || (hit.attrs?.hdr ?? []).some((v) => f.hdr.has(v)) },
  {
    dim: "audio",
    pass: (hit, f) => !f.audio.size || (hit.attrs?.audio ?? []).some((v) => f.audio.has(v)),
  },
  {
    dim: "group",
    pass: (hit, f) =>
      !f.group.size || (!!hit.attrs?.release_group && f.group.has(hit.attrs.release_group)),
  },
];

function matchesFilters(hit: TorrentHit, f: Filters): boolean {
  return FILTER_DIMENSIONS.every((d) => d.pass(hit, f));
}

/* —— 聚合：从当前结果集统计各维度可选值与计数 —— */

interface FacetValue {
  value: string;
  count: number;
}

/** 数值型枚举维度（季/集/年份）的可选值与命中计数。 */
interface NumericFacetValue {
  value: number;
  count: number;
}

interface Facets {
  resolution: FacetValue[];
  years: NumericFacetValue[]; // 降序
  seasons: NumericFacetValue[]; // 升序，观测到多少列多少
  episodes: NumericFacetValue[];
  source: FacetValue[];
  codec: FacetValue[];
  hdr: FacetValue[];
  audio: FacetValue[];
  groups: FacetValue[];
  /** 站点维度按 site_id 计数（chip 列表来自站点搜索状态，这里只供数字）。 */
  sites: Map<string, number>;
}

/** 各维度的值→命中数映射（一次遍历的原始计数，排序/截断在 aggregateFacets 里做）。 */
interface FacetMaps {
  resolution: Map<string, number>;
  source: Map<string, number>;
  codec: Map<string, number>;
  hdr: Map<string, number>;
  audio: Map<string, number>;
  groups: Map<string, number>;
  sites: Map<string, number>;
  years: Map<number, number>;
  seasons: Map<number, number>;
  episodes: Map<number, number>;
}

/**
 * 遍历结果集统计各维度计数。filters 为 null 时统计全量（决定 chip 的取值范围与
 * 展示顺序）；传入 filters 时做「自排除」计数——某条结果只有在通过了*其他所有*
 * 维度的筛选后，才计入某维度的数字。组内多选是"或"，所以本维度自身不设限，
 * 这样每个标签上的数字始终等于「选中它之后会看到的条数」。
 * 实现上按未通过的维度数分流：全通过 → 计入所有维度；只挂在一个维度 →
 * 只计入该维度（选中该维度里对应的值就能救回它）；挂两个以上 → 不计入任何维度。
 */
function collectFacetMaps(items: TorrentHit[], filters: Filters | null): FacetMaps {
  const maps: FacetMaps = {
    resolution: new Map(),
    source: new Map(),
    codec: new Map(),
    hdr: new Map(),
    audio: new Map(),
    groups: new Map(),
    sites: new Map(),
    years: new Map(),
    seasons: new Map(),
    episodes: new Map(),
  };
  const bump = (map: Map<string, number>, key: string) =>
    map.set(key, (map.get(key) ?? 0) + 1);
  const bumpNum = (map: Map<number, number>, key: number) =>
    map.set(key, (map.get(key) ?? 0) + 1);

  for (const hit of items) {
    // only 非空 = 这条结果只挂在该维度上，仅计入它；全通过时保持 null（计入所有维度）
    let only: FilterDim | null = null;
    if (filters) {
      const failed = FILTER_DIMENSIONS.filter((d) => !d.pass(hit, filters));
      if (failed.length > 1) continue;
      if (failed.length === 1) only = failed[0].dim;
    }
    const want = (dim: FilterDim) => only === null || only === dim;

    if (want("site")) bump(maps.sites, hit.site_id);
    const a = hit.attrs;
    if (!a) continue;
    if (want("year") && a.year !== null) bumpNum(maps.years, a.year);
    // 季/集：一个种子可能覆盖多季/多集（S01-S05 / E01-E06），逐值计数
    if (want("season")) for (const s of a.seasons) bumpNum(maps.seasons, s);
    if (want("episode")) for (const e of a.episodes) bumpNum(maps.episodes, e);
    if (want("resolution") && a.resolution) bump(maps.resolution, a.resolution);
    if (want("source")) {
      if (a.media_source) bump(maps.source, a.media_source);
      if (a.remux) bump(maps.source, "Remux");
    }
    if (want("codec") && a.video_codec) bump(maps.codec, a.video_codec);
    if (want("hdr")) for (const v of a.hdr) bump(maps.hdr, v);
    if (want("audio")) for (const v of a.audio) bump(maps.audio, v);
    if (want("group") && a.release_group) bump(maps.groups, a.release_group);
  }
  return maps;
}

function aggregateFacets(items: TorrentHit[], filters: Filters): Facets {
  // 全量计数定「有哪些 chip、按什么顺序排」——筛选变化时 chip 不增删、不换位，
  // 已选中的值也不会因为计数归零而消失（否则没法取消勾选）；数字才跟着筛选走。
  const full = collectFacetMaps(items, null);
  const counts = hasActiveFilters(filters) ? collectFacetMaps(items, filters) : full;

  const toSorted = (key: keyof FacetMaps & string, cap: number): FacetValue[] =>
    [...(full[key] as Map<string, number>).entries()]
      .sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]))
      .slice(0, cap)
      .map(([value]) => ({
        value,
        count: (counts[key] as Map<string, number>).get(value) ?? 0,
      }));
  const toNumeric = (key: "years" | "seasons" | "episodes", order: 1 | -1): NumericFacetValue[] =>
    [...full[key].entries()]
      .sort((x, y) => (x[0] - y[0]) * order)
      .map(([value]) => ({ value, count: counts[key].get(value) ?? 0 }));

  return {
    // 分辨率是唯一的常驻 chips 维度，上限收紧防止挤爆一行；其余在弹层里，可放宽
    resolution: toSorted("resolution", 8),
    years: toNumeric("years", -1),
    seasons: toNumeric("seasons", 1),
    episodes: toNumeric("episodes", 1),
    source: toSorted("source", 50),
    codec: toSorted("codec", 50),
    hdr: toSorted("hdr", 50),
    audio: toSorted("audio", 50),
    groups: toSorted("groups", 50),
    sites: counts.sites,
  };
}

/* —— 作品聚合：分组视图的组头元数据 —— */

interface EntityGroup {
  key: string;
  /** 中文主名 / 外文主名：组头展示用，组内首个观测到的非空值 */
  nameZh: string | null;
  nameEn: string | null;
  year: number | null;
  mediaType: string | null;
  /** 题材轴（动漫/纪录片…），组内首个观测值 */
  contentType: string | null;
}

/**
 * 从全量结果收集各作品的组头元数据（键 → 名称/年份/类型）。
 * 组内字段取首个非空观测值：同组种子的解析结果一致性很高，缺失的由后来者补齐。
 */
function collectEntities(items: TorrentHit[]): Map<string, EntityGroup> {
  const groups = new Map<string, EntityGroup>();
  for (const hit of items) {
    const key = entityKeyOf(hit);
    if (key === ENTITY_UNPARSED) continue;
    const a = hit.attrs;
    const g = groups.get(key);
    if (g) {
      g.nameZh ??= a?.titles_zh?.[0] ?? null;
      g.nameEn ??= a?.titles_en?.[0] ?? null;
      g.year ??= a?.year ?? null;
      g.contentType ??= a?.content_type ?? null;
    } else {
      groups.set(key, {
        key,
        nameZh: a?.titles_zh?.[0] ?? null,
        nameEn: a?.titles_en?.[0] ?? null,
        year: a?.year ?? null,
        mediaType: a?.media_type ?? null,
        contentType: a?.content_type ?? null,
      });
    }
  }
  return groups;
}

export function SearchResults({ query, onResearch }: SearchResultsProps) {
  const [phase, setPhase] = useState<Phase>("connecting");
  const [fatalError, setFatalError] = useState<string | null>(null);
  // 结果按 site_result 事件到达顺序累加——快站先上屏，排序视图实时并入新结果
  const [items, setItems] = useState<TorrentHit[]>([]);
  const [siteProgress, setSiteProgress] = useState<SiteProgress[]>([]);
  // 整次搜索的总耗时（done 事件 / 快照回放），站点状态弹层的汇总行展示
  const [totalElapsedMs, setTotalElapsedMs] = useState<number | null>(null);
  // 快照预览态：非空 = 当前展示的是历史快照（值为快照生成时间，供提示条换算年龄）
  const [snapshotAt, setSnapshotAt] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  // 排序偏好跨搜索保留（改排序不代表想对下一次搜索用别的排序）。
  // 初值优先取地址栏的 sort 参数（刷新 / 分享链接直达时恢复），否则默认做种数降序。
  // 本子树仅在登录确认后于客户端首次渲染（见 AuthGate 未登录返回 null），
  // 故可安全读取 window，不会有 SSR 水合不一致。
  const [sort, setSort] = useState<SortState>(readSortFromUrl);
  // 结果视图三选一：分组（默认）/ 列表（平铺种子）/ 图览。图览初值跟随本次搜索
  // 的范围——scope.posterMode 已由 parseSearchQuery 从地址栏 poster 参数解出，
  // 刷新时经这里自然恢复；平铺偏好走地址栏 view=list（见下方 URL 同步）。
  // 工具栏右侧的分段可随时切换，只影响展示，不写回任何设置。
  const [view, setView] = useState<ResultView>(() => initialView(query.scope.posterMode));

  // 视图态（排序 / 展示视图）实时同步进地址栏：让「点一下就变」的操作也能刷新保留、分享还原。
  // 用原生 replaceState 而非 router.replace——只改地址不触发 Next 路由/useSearchParams 更新，
  // 因此不会误判为新搜索而重新发起流式请求；默认值（做种降序 / 分组视图）不写入，保持地址简洁。
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    if (sort.key === "seeders" && sort.dir === "desc") params.delete("sort");
    else params.set("sort", `${sort.key}:${sort.dir}`);
    if (view === "poster") params.set("poster", "1");
    else params.delete("poster");
    if (view === "list") params.set("view", "list");
    else params.delete("view");
    const qs = params.toString();
    window.history.replaceState(
      window.history.state,
      "",
      qs ? `${window.location.pathname}?${qs}` : window.location.pathname,
    );
  }, [sort, view]);

  // 查询（关键词/范围）变化即重新发起流式搜索；用 AbortController 中断上一条流，避免竞态。
  // 新搜索同时重置筛选条件——旧结果的筛选对新结果没有意义。
  // 依赖用 query 对象本身：app-shell 每次提交都会构造新对象，成员深比较无必要。
  useEffect(() => {
    const controller = new AbortController();
    setPhase("connecting");
    setFatalError(null);
    setItems([]);
    setSiteProgress([]);
    setTotalElapsedMs(null);
    setSnapshotAt(null);
    setFilters(emptyFilters());
    // 图览跟随新搜索的范围预设；列表/分组是跨搜索保留的用户偏好（同排序），
    // 从图览预设离开时回到默认的分组视图
    setView((prev) =>
      query.scope.posterMode ? "poster" : prev === "poster" ? "group" : prev,
    );

    // 快照预览：不打扰任何站点，直接加载历史留存的结果集一次性上屏。
    // 站点状态（含逐站耗时/失败原因）从快照回放，过程面板/进度条自然不出现。
    if (query.snapshotId != null) {
      fetchSearchSnapshot(query.snapshotId, { signal: controller.signal })
        .then((snap) => {
          setItems(snap.items);
          setSiteProgress(
            snap.sites.map((s) => ({
              site_id: s.site_id,
              site_name: s.site_name,
              phase: s.error ? "error" : "ok",
              count: s.count,
              error: s.error,
              elapsed_ms: s.elapsed_ms ?? null,
            })),
          );
          setTotalElapsedMs(snap.elapsed_ms ?? null);
          setSnapshotAt(snap.snapshot_at);
          setPhase("done");
        })
        .catch((err: unknown) => {
          if (controller.signal.aborted) return;
          setPhase("error");
          setFatalError(
            err instanceof Error ? err.message : "快照加载失败，请稍后重试",
          );
        });
      return () => controller.abort();
    }

    // 用最新事件更新单个站点的进度
    const patchSite = (siteId: string, patch: Partial<SiteProgress>) =>
      setSiteProgress((prev) =>
        prev.map((s) => (s.site_id === siteId ? { ...s, ...patch } : s)),
      );

    streamSearchTorrents(
      { keyword: query.keyword, scope: query.scope },
      (event) => {
        switch (event.type) {
          case "start":
            setPhase("streaming");
            setSiteProgress(
              event.data.sites.map((s) => ({
                ...s,
                phase: "searching",
                count: 0,
                error: null,
                elapsed_ms: null,
              })),
            );
            break;
          case "site_start":
            break; // 逐站进度由 start 事件建好的 siteProgress 承载，无需额外处理
          case "site_result": {
            const d = event.data;
            setItems((prev) => [...prev, ...d.items]);
            patchSite(d.site_id, { phase: "ok", count: d.count, elapsed_ms: d.elapsed_ms });
            break;
          }
          case "site_error": {
            const d = event.data;
            patchSite(d.site_id, { phase: "error", error: d.error, elapsed_ms: d.elapsed_ms });
            break;
          }
          case "done":
            setTotalElapsedMs(event.data.elapsed_ms);
            setPhase("done");
            break;
        }
      },
      { signal: controller.signal },
    ).catch((err: unknown) => {
      if (controller.signal.aborted) return; // 被新搜索中断，忽略
      setPhase("error");
      setFatalError(err instanceof Error ? err.message : "搜索失败，请稍后重试");
    });
    return () => controller.abort();
  }, [query]);

  const facets = useMemo(() => aggregateFacets(items, filters), [items, filters]);
  const entities = useMemo(() => collectEntities(items), [items]);
  const filtered = useMemo(
    () => items.filter((hit) => matchesFilters(hit, filters)),
    [items, filters],
  );
  const sorted = useMemo(() => {
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort(
      (x, y) => (sortValue(x, sort.key) - sortValue(y, sort.key)) * dir,
    );
  }, [filtered, sort]);
  const filtering = hasActiveFilters(filters);

  // 已完成站点的状态视图：筛选弹层的「站点」组与条件回显只认已出结果的站点
  const settledStatuses: SiteSearchStatus[] = useMemo(
    () =>
      siteProgress
        .filter((s) => s.phase !== "searching")
        .map((s) => ({
          site_id: s.site_id,
          site_name: s.site_name,
          count: s.count,
          error: s.error,
          elapsed_ms: s.elapsed_ms,
        })),
    [siteProgress],
  );
  const settledCount = settledStatuses.length;
  const streaming = phase === "connecting" || phase === "streaming";

  return (
    <div className="relative flex h-full flex-col">
      {/* 顶部（常驻两行 + 按需一行）：
          状态行 = 搜索词/计数（左）+ 快照药丸/站点状态聚合 chip（右）
          工具栏 = 类型分段/分辨率（左）+ 筛选/排序/视图（右）
          条件回显行只在筛选激活时出现；流式期间再加一条 2px 进度线 */}
      {/* pt-4：上方还有 /search 页的垂直选项卡行（影视 | 站点资源），间距略收 */}
      <header className="relative z-20 shrink-0 px-6 pb-3 pt-4">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
          <h1 className="text-on-image text-[18px] font-semibold tracking-[-0.01em] text-white">
            “{query.keyword}”
          </h1>
          {query.scope.label && (
            <span className="rounded-full bg-black/30 px-2.5 py-0.5 text-[11px] text-[var(--accent)] backdrop-blur-sm">
              {query.scope.label}
            </span>
          )}
          {phase === "streaming" && (
            <span className="text-on-image flex items-center gap-1.5 text-[12px] text-[rgba(243,245,249,0.75)]">
              <span className="size-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
              已找到 {items.length} 条
            </span>
          )}
          {phase === "done" && (
            <span className="text-on-image text-[12px] text-[rgba(243,245,249,0.75)]">
              {filtering
                ? `筛选后 ${filtered.length} / 共 ${items.length} 条`
                : `共 ${items.length} 条结果`}
            </span>
          )}

          {/* 右侧状态组：快照年龄 + 重搜、站点状态聚合（点开看逐站详情） */}
          <div className="ml-auto flex items-center gap-2">
            {snapshotAt && phase === "done" && (
              <>
                <span
                  title="这是历史留存的结果快照，站点数据（做种数/促销/链接）可能已变化"
                  className="flex items-center gap-1.5 rounded-full border border-[#6aa7ff]/30 bg-[#6aa7ff]/12 px-2.5 py-1 text-[11px] text-[#b9d4ff] backdrop-blur-sm"
                >
                  <svg
                    viewBox="0 0 24 24"
                    className="size-[13px] shrink-0"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.8}
                    strokeLinecap="round"
                    aria-hidden="true"
                  >
                    <circle cx="12" cy="12" r="8.5" />
                    <path d="M12 7.5V12l3 2" />
                  </svg>
                  {formatRelativeTime(snapshotAt)}的快照
                </span>
                {onResearch && (
                  <button
                    type="button"
                    onClick={() => onResearch(query.keyword, query.scope)}
                    className="btn-accent rounded-full px-2.5 py-1 text-[11px] font-medium"
                  >
                    重新搜索
                  </button>
                )}
              </>
            )}
            <SiteStatusSummary
              sites={siteProgress}
              streaming={streaming}
              totalElapsedMs={totalElapsedMs}
            />
          </div>
        </div>
        {phase === "done" && siteProgress.length === 0 && !snapshotAt && (
          <p className="text-on-image mt-2 text-[12px] text-[rgba(243,245,249,0.7)]">
            当前没有「已启用且验证通过」的站点，请先在设置里配置站点。
          </p>
        )}
        {siteProgress.length > 0 && (
          <>
            <FilterToolbar
              sites={settledStatuses}
              facets={facets}
              filters={filters}
              onChange={setFilters}
              sort={sort}
              onSortChange={setSort}
              resultCount={filtered.length}
              view={view}
              onViewChange={setView}
            />
            <AppliedChips
              sites={settledStatuses}
              filters={filters}
              onChange={setFilters}
            />
          </>
        )}
        {streaming && (
          <SearchProgressBar settled={settledCount} total={siteProgress.length} />
        )}
      </header>

      {/* 主体：结果随 site_result 事件渐进出现，首批结果到达前保持骨架屏 */}
      <div className="scroll-thin relative z-0 min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        {streaming && items.length === 0 && (
          <SkeletonList siteCount={siteProgress.length} />
        )}
        {phase === "error" && (
          <EmptyHint title="搜索出错" hint={fatalError ?? "搜索失败，请稍后重试"} />
        )}
        {sorted.length > 0 ? (
          view === "poster" ? (
            <PosterResults hits={sorted} />
          ) : view === "group" && entities.size >= 2 ? (
            // 分组视图且识别出至少两部作品：按作品分组（组头 = 片名中英文 + 年份）
            <GroupedResults hits={sorted} entities={entities} />
          ) : (
            // 列表视图；分组视图下单作品/全未识别时也退回平铺
            <ul className="space-y-2">
              {sorted.map((hit) => (
                <TorrentRow
                  key={`${hit.site_id}:${hit.torrent_id}`}
                  hit={hit}
                  showRawTitles={view === "list"}
                />
              ))}
            </ul>
          )
        ) : items.length ? (
          <EmptyHint
            title="没有符合筛选条件的结果"
            hint="放宽或清除顶部的筛选条件试试。"
          />
        ) : phase === "done" ? (
          <EmptyHint
            title="没有找到匹配的资源"
            hint="换个关键词，或检查已配置站点是否验证通过。"
          />
        ) : null}
      </div>

    </div>
  );
}

/* —— 通用小件 —— */

const CHIP_ACTIVE_CLS =
  "border-white/[0.2] bg-white/[0.14] text-[var(--text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]";
const CHIP_IDLE_CLS =
  "border-white/[0.08] bg-white/[0.035] text-[var(--text-muted)] hover:border-white/[0.15] hover:bg-white/[0.07] hover:text-[var(--text)]";
const SEGMENT_ACTIVE_CLS =
  "bg-white/[0.14] font-medium text-[var(--text)] shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]";
const SEGMENT_IDLE_CLS =
  "text-[var(--text-muted)] hover:bg-white/[0.05] hover:text-[var(--text)]";

/** 可切换的筛选 chip（带命中计数）。 */
function FacetChip({
  label,
  count,
  active,
  onToggle,
}: {
  label: string;
  count: number;
  active: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={active}
      className={`h-7 rounded-full border px-2.5 text-[11px] transition-all ${active ? CHIP_ACTIVE_CLS : CHIP_IDLE_CLS}`}
    >
      {label}
      <span className="tnum ml-1 opacity-60">{count}</span>
    </button>
  );
}

/** 在 Set 型筛选维度上切换一个值（不可变更新）。 */
function toggleIn<T>(set: Set<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

/* —— 作品分组视图 —— */

/** 组内默认展示的版本数上限；超出折叠成「展开其余 N 个版本」。 */
const GROUP_ROW_CAP = 4;

/**
 * 按作品分组的结果视图（识别出 ≥2 部作品时替代平铺，见调用处开关）。
 *
 * 分组顺序 = 按当前排序遍历结果时各作品的「首次出现」序——排序取做种数降序时，
 * 头部组自然是含最热种子的作品，全局排序的语义不丢；组内行序同理天然有序。
 * 未识别桶（解析不出片名）固定沉底，组内按原始种子名平铺。
 * 组头可点击折叠；组内超过 GROUP_ROW_CAP 条时余量收起，按需展开。
 */
function GroupedResults({
  hits,
  entities,
}: {
  hits: TorrentHit[];
  entities: Map<string, EntityGroup>;
}) {
  // 折叠/展开状态按组键记忆：筛选与排序变化不清空；新搜索时组件整体重挂载自然重置
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [uncapped, setUncapped] = useState<Set<string>>(() => new Set());

  const buckets: { key: string; rows: TorrentHit[] }[] = [];
  const indexOf = new Map<string, number>();
  for (const hit of hits) {
    const key = entityKeyOf(hit);
    const i = indexOf.get(key);
    if (i === undefined) {
      indexOf.set(key, buckets.length);
      buckets.push({ key, rows: [hit] });
    } else {
      buckets[i].rows.push(hit);
    }
  }
  const unparsedAt = indexOf.get(ENTITY_UNPARSED);
  if (unparsedAt !== undefined && unparsedAt !== buckets.length - 1) {
    const [u] = buckets.splice(unparsedAt, 1);
    buckets.push(u);
  }

  return (
    <div className="space-y-4">
      {buckets.map(({ key, rows }) => {
        const meta = entities.get(key);
        const open = !collapsed.has(key);
        const showAll = uncapped.has(key) || rows.length <= GROUP_ROW_CAP;
        const shown = showAll ? rows : rows.slice(0, GROUP_ROW_CAP);
        return (
          <section key={key}>
            <GroupHeader
              meta={meta ?? null}
              rows={rows}
              open={open}
              onToggle={() => setCollapsed((prev) => toggleIn(prev, key))}
            />
            {open && (
              <>
                <ul className="mt-2 space-y-2 pl-4">
                  {shown.map((hit) => (
                    <TorrentRow key={`${hit.site_id}:${hit.torrent_id}`} hit={hit} grouped />
                  ))}
                </ul>
                {!showAll && (
                  <button
                    type="button"
                    onClick={() => setUncapped((prev) => toggleIn(prev, key))}
                    className="ml-4 mt-2 px-1 py-0.5 text-[12px] text-[var(--accent-2)] transition-colors hover:text-[var(--text)]"
                  >
                    展开其余 {rows.length - GROUP_ROW_CAP} 个版本 ▾
                  </button>
                )}
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** 组头：片名中英文 + 年份/类型/题材 + 组内聚合徽标（版本数、最高分辨率、免费数）。 */
function GroupHeader({
  meta,
  rows,
  open,
  onToggle,
}: {
  meta: EntityGroup | null;
  rows: TorrentHit[];
  open: boolean;
  onToggle: () => void;
}) {
  const unparsed = meta === null;
  const freeCount = rows.filter((h) => h.free || h.download_volume_factor === 0).length;
  const topRes = maxResolution(rows);
  const info = unparsed
    ? "按原始名展示"
    : [
        meta.year,
        meta.mediaType && MEDIA_TYPE_LABEL[meta.mediaType],
        meta.contentType && CONTENT_TYPE_LABEL[meta.contentType],
      ]
        .filter(Boolean)
        .join(" · ");
  const header = (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      className="flex w-full items-center gap-3 rounded-2xl border border-white/[0.09] bg-white/[0.045] px-4 py-2.5 text-left backdrop-blur-xl transition-colors hover:border-white/[0.16] hover:bg-white/[0.07]"
    >
      <span
        className={`shrink-0 text-[10px] text-[var(--text-faint)] transition-transform ${open ? "rotate-90" : ""}`}
      >
        ▶
      </span>
      <span className="min-w-0 flex-1 truncate">
        <span className="text-[14.5px] font-semibold text-[var(--text)]">
          {unparsed ? "未识别" : (meta.nameZh ?? meta.nameEn)}
        </span>
        {!unparsed && meta.nameZh && meta.nameEn && (
          <span className="ml-2 text-[12px] text-[var(--text-muted)]">{meta.nameEn}</span>
        )}
        {info && (
          <span className="tnum ml-2 text-[12px] text-[var(--text-faint)]">{info}</span>
        )}
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        <span className="tnum rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-[var(--text-muted)]">
          {rows.length} 个版本
        </span>
        {topRes && (
          <span className="rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-[var(--accent-2)]">
            最高 {topRes}
          </span>
        )}
        {freeCount > 0 && (
          <span className="tnum rounded-md bg-[#4ade80]/15 px-1.5 py-0.5 text-[10px] font-semibold text-[#79d193]">
            {freeCount} 个免费
          </span>
        )}
      </span>
    </button>
  );
  // 悬停组头直接浮出 PT 站原始标题（主标题 + 中文副标题），不做任何加工——
  // 组头是解析结果，原始串在这里对照；取组内当前排序的第一条为代表
  const sample = rows[0];
  if (unparsed || !sample || (!sample.title && !sample.subtitle)) return header;
  return (
    <Tooltip
      placement="top-start"
      content={
        <div className="space-y-1">
          {sample.title && <p>{sample.title}</p>}
          {sample.subtitle && <p className="text-[var(--text-muted)]">{sample.subtitle}</p>}
        </div>
      }
    >
      {header}
    </Tooltip>
  );
}

/** 组内最高分辨率（按数字位比较，2160p > 1080p > 720p），无解析值返回 null。 */
function maxResolution(rows: TorrentHit[]): string | null {
  let best: string | null = null;
  let bestNum = -1;
  for (const hit of rows) {
    const res = hit.attrs?.resolution;
    if (!res) continue;
    const num = parseInt(res, 10);
    if (Number.isFinite(num) && num > bestNum) {
      bestNum = num;
      best = res;
    }
  }
  return best;
}

/* —— 第一层：常驻工具栏 —— */

function FilterToolbar({
  sites,
  facets,
  filters,
  onChange,
  sort,
  onSortChange,
  resultCount,
  view,
  onViewChange,
}: {
  sites: SiteSearchStatus[];
  facets: Facets;
  filters: Filters;
  onChange: (f: Filters) => void;
  sort: SortState;
  onSortChange: (s: SortState) => void;
  resultCount: number;
  view: ResultView;
  onViewChange: (v: ResultView) => void;
}) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const badge = sheetSelectionCount(filters);

  return (
    <div className="mt-3 flex flex-wrap items-center gap-3 rounded-2xl border border-white/[0.07] bg-black/[0.14] p-2.5 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)] backdrop-blur-xl">
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
        {/* 排序：高频操作，置于工具栏最前 */}
        <select
          aria-label="排序依据"
          className="h-7 rounded-full border border-white/[0.08] bg-black/[0.22] px-3 text-[11px] text-[var(--text-muted)] outline-none transition-colors hover:border-white/[0.15] hover:text-[var(--text)]"
          value={sort.key}
          onChange={(e) => onSortChange({ ...sort, key: e.target.value as SortKey })}
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.key} value={o.key}>
              按{o.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          title={sort.dir === "desc" ? "当前降序，点击切换为升序" : "当前升序，点击切换为降序"}
          onClick={() =>
            onSortChange({ ...sort, dir: sort.dir === "desc" ? "asc" : "desc" })
          }
          aria-label={sort.dir === "desc" ? "切换为升序" : "切换为降序"}
          className={`grid size-7 place-items-center rounded-full border text-[13px] transition-all ${CHIP_IDLE_CLS}`}
        >
          {sort.dir === "desc" ? "↓" : "↑"}
        </button>

        {/* 分辨率：唯一的高频筛选维度，常驻 chips */}
        {facets.resolution.map(({ value, count }) => (
        <FacetChip
          key={value}
          label={value}
          count={count}
          active={filters.resolution.has(value)}
          onToggle={() =>
            onChange({ ...filters, resolution: toggleIn(filters.resolution, value) })
          }
        />
        ))}
      </div>

      {/* 右侧：视图切换（分组/列表/图览）+ 筛选弹层入口（最后） */}
      <div className="relative ml-auto flex flex-wrap items-center justify-end gap-1.5">
        {/* 视图分段切换：只切换当次搜索的展示方式，图览的长期默认值在自定义分类里设置 */}
        <div className="flex items-center rounded-full border border-white/[0.08] bg-black/[0.16] p-0.5">
          {(
            [
              { v: "group", label: "分组", Icon: LayersIcon, hint: "分组：按作品聚合，组内是各版本" },
              { v: "list", label: "列表", Icon: ListIcon, hint: "列表：平铺展示每条种子" },
              { v: "poster", label: "图览", Icon: PhotoIcon, hint: "图览：带海报的结果以图墙展示" },
            ] as const
          ).map(({ v, label, Icon, hint }) => (
            <button
              key={v}
              type="button"
              aria-pressed={view === v}
              title={hint}
              onClick={() => onViewChange(v)}
              className={`flex h-6 items-center gap-1 rounded-full px-2.5 text-[11px] transition-all ${view === v ? SEGMENT_ACTIVE_CLS : SEGMENT_IDLE_CLS}`}
            >
              <Icon className="size-3.5" />
              {label}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setSheetOpen((v) => !v)}
          aria-expanded={sheetOpen}
          className={`flex h-7 items-center gap-1.5 rounded-full border px-3 text-[11px] transition-all ${
            badge > 0 ? CHIP_ACTIVE_CLS : CHIP_IDLE_CLS
          }`}
        >
          筛选
          {badge > 0 && (
            <span className="tnum rounded-full bg-[var(--accent)]/25 px-1.5 text-[10px]">
              {badge}
            </span>
          )}
          <span className={`text-[9px] opacity-70 transition-transform ${sheetOpen ? "rotate-180" : ""}`}>▾</span>
        </button>

        {sheetOpen && (
          <FilterSheet
            sites={sites}
            facets={facets}
            filters={filters}
            onChange={onChange}
            resultCount={resultCount}
            onClose={() => setSheetOpen(false)}
          />
        )}
      </div>
    </div>
  );
}

/* —— 第二层：筛选弹层 —— */

function SheetGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-[11px] font-medium text-[var(--text-faint)]">{title}</p>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function FilterSheet({
  sites,
  facets,
  filters,
  onChange,
  resultCount,
  onClose,
}: {
  sites: SiteSearchStatus[];
  facets: Facets;
  filters: Filters;
  onChange: (f: Filters) => void;
  resultCount: number;
  onClose: () => void;
}) {
  const toggle = (key: SheetKey, value: string | number) =>
    onChange({ ...filters, [key]: toggleIn(filters[key] as Set<string | number>, value) });

  const okSites = sites.filter((s) => !s.error);

  return (
    <>
      {/* 点击空白处关闭 */}
      <div className="fixed inset-0 z-20" onClick={onClose} />
      <div className="absolute right-0 top-full z-30 mt-2 max-h-[60vh] w-[560px] max-w-[82vw] overflow-y-auto rounded-2xl border border-white/[0.12] bg-[rgba(14,16,22,0.96)] p-4 shadow-2xl backdrop-blur-2xl">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-[13px] font-medium text-[var(--text)]">筛选结果</p>
            <p className="mt-0.5 text-[11px] text-[var(--text-faint)]">可多选，分类之间组合生效</p>
          </div>
          <button type="button" onClick={onClose} className={`grid size-7 place-items-center rounded-full border text-sm ${CHIP_IDLE_CLS}`} aria-label="关闭筛选">
            ×
          </button>
        </div>
        <div className="space-y-3.5">
          {okSites.length > 0 && (
            <SheetGroup title="站点">
              {okSites.map((s) => (
                <FacetChip
                  key={s.site_id}
                  label={s.site_name}
                  // 计数走分面统计而非站点原始结果数：跟随其他维度的筛选联动
                  count={facets.sites.get(s.site_id) ?? 0}
                  active={filters.site.has(s.site_id)}
                  onToggle={() => toggle("site", s.site_id)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.years.length > 0 && (
            <SheetGroup title="年份">
              {facets.years.map((y) => (
                <FacetChip
                  key={y.value}
                  label={String(y.value)}
                  count={y.count}
                  active={filters.year.has(y.value)}
                  onToggle={() => toggle("year", y.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.seasons.length > 0 && (
            <SheetGroup title="季">
              {facets.seasons.map((s) => (
                <FacetChip
                  key={s.value}
                  label={`第${s.value}季`}
                  count={s.count}
                  active={filters.season.has(s.value)}
                  onToggle={() => toggle("season", s.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.episodes.length > 0 && (
            <SheetGroup title="集">
              {facets.episodes.map((ep) => (
                <FacetChip
                  key={ep.value}
                  label={`第${ep.value}集`}
                  count={ep.count}
                  active={filters.episode.has(ep.value)}
                  onToggle={() => toggle("episode", ep.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.source.length > 0 && (
            <SheetGroup title="片源">
              {facets.source.map((v) => (
                <FacetChip
                  key={v.value}
                  label={v.value}
                  count={v.count}
                  active={filters.source.has(v.value)}
                  onToggle={() => toggle("source", v.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.codec.length > 0 && (
            <SheetGroup title="视频编码">
              {facets.codec.map((v) => (
                <FacetChip
                  key={v.value}
                  label={v.value}
                  count={v.count}
                  active={filters.codec.has(v.value)}
                  onToggle={() => toggle("codec", v.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.hdr.length > 0 && (
            <SheetGroup title="HDR">
              {facets.hdr.map((v) => (
                <FacetChip
                  key={v.value}
                  label={v.value}
                  count={v.count}
                  active={filters.hdr.has(v.value)}
                  onToggle={() => toggle("hdr", v.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.audio.length > 0 && (
            <SheetGroup title="音频">
              {facets.audio.map((v) => (
                <FacetChip
                  key={v.value}
                  label={v.value}
                  count={v.count}
                  active={filters.audio.has(v.value)}
                  onToggle={() => toggle("audio", v.value)}
                />
              ))}
            </SheetGroup>
          )}
          {facets.groups.length > 0 && (
            <SheetGroup title="压制组">
              {facets.groups.map((v) => (
                <FacetChip
                  key={v.value}
                  label={v.value}
                  count={v.count}
                  active={filters.group.has(v.value)}
                  onToggle={() => toggle("group", v.value)}
                />
              ))}
            </SheetGroup>
          )}
        </div>

        <div className="mt-4 flex items-center justify-between border-t border-white/[0.08] pt-3">
          <button
            type="button"
            onClick={() => onChange(emptyFilters())}
            className="btn-glass h-8 px-3 text-[11px] text-[var(--text-muted)]"
          >
            清除全部
          </button>
          <button
            type="button"
            onClick={onClose}
            className="btn-accent h-8 rounded-full px-4 text-[11px] font-medium"
          >
            查看 {resultCount} 条结果
          </button>
        </div>
      </div>
    </>
  );
}

/* —— 第三层：已应用条件回显行 —— */

function AppliedChips({
  sites,
  filters,
  onChange,
}: {
  sites: SiteSearchStatus[];
  filters: Filters;
  onChange: (f: Filters) => void;
}) {
  if (sheetSelectionCount(filters) === 0) return null;

  const siteName = (id: string) =>
    sites.find((s) => s.site_id === id)?.site_name ?? id;
  const labelOf = (key: SheetKey, value: string | number): string => {
    if (key === "site") return siteName(String(value));
    if (key === "season") return `第${value}季`;
    if (key === "episode") return `第${value}集`;
    return String(value);
  };

  const chips = SHEET_KEYS.flatMap((key) =>
    [...filters[key]].map((value) => ({ key, value })),
  );

  return (
    <div className="mt-2 flex flex-wrap items-center gap-1.5 px-1">
      <span className="text-[11px] text-[var(--text-faint)]">生效条件</span>
      {chips.map(({ key, value }) => (
        <button
          key={`${key}:${value}`}
          type="button"
          onClick={() =>
            onChange({ ...filters, [key]: toggleIn(filters[key] as Set<string | number>, value) })
          }
          className={`group flex h-6 items-center gap-1 rounded-full border px-2 text-[11px] transition-all hover:bg-white/[0.2] ${CHIP_ACTIVE_CLS}`}
        >
          {labelOf(key, value)}
          <span className="opacity-60 transition-opacity group-hover:opacity-100">×</span>
        </button>
      ))}
      <button
        type="button"
        onClick={() => onChange(emptyFilters())}
        className="ml-1 text-[11px] text-[var(--text-faint)] transition-colors hover:text-[var(--text)]"
      >
        清除全部
      </button>
    </div>
  );
}

/* —— 站点状态：状态行右侧的聚合 chip + 逐站详情弹层 —— */

/**
 * 把逐站进度压缩成一枚聚合 chip（原来独占一行的逐站 chips 太占页头高度）：
 * 搜索中显示「x/N 站点」带脉冲点，完成后显示「N 站点（· M 失败）」，
 * 状态点颜色 = 整体健康度（搜索中琥珀脉冲 / 全部成功绿 / 有失败红）。
 *
 * 点击弹出逐站详情，每站一行三要素齐全：**状态**（色点+文字）、**命中条数**、
 * **耗时**——成功失败都有耗时，十几秒后才失败的一眼可辨是超时，秒失败的多半
 * 是认证/解析问题。底部汇总行给出整次搜索的总耗时；快照回放时数据同样齐全。
 */
function SiteStatusSummary({
  sites,
  streaming,
  totalElapsedMs,
}: {
  sites: SiteProgress[];
  streaming: boolean;
  totalElapsedMs: number | null;
}) {
  const [open, setOpen] = useState(false);

  if (sites.length === 0) return null;

  const settled = sites.filter((s) => s.phase !== "searching").length;
  const failed = sites.filter((s) => s.phase === "error").length;
  const dotCls = streaming
    ? "animate-pulse bg-[var(--accent)]"
    : failed > 0
      ? "bg-[#ff6b6b]"
      : "bg-[#4ade80]";

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="查看各站点的搜索详情"
        className="flex items-center gap-1.5 rounded-full border border-white/[0.12] bg-white/[0.05] px-2.5 py-1 text-[11px] text-[var(--text-muted)] backdrop-blur-sm transition-colors hover:border-white/[0.22] hover:text-[var(--text)]"
      >
        <span className={`size-1.5 rounded-full ${dotCls}`} />
        {streaming ? (
          <span className="tnum">{settled}/{sites.length} 站点</span>
        ) : (
          <>
            <span className="tnum">{sites.length} 站点</span>
            {failed > 0 && (
              <span className="tnum text-[#ff9a9a]">· {failed} 失败</span>
            )}
          </>
        )}
        <span className="text-[9px] opacity-70">▾</span>
      </button>

      {open && (
        <>
          {/* 点击空白处关闭（与筛选弹层同款交互） */}
          <div className="fixed inset-0 z-20" onClick={() => setOpen(false)} />
          <div className="absolute right-0 top-full z-30 mt-2 w-[320px] max-w-[82vw] rounded-2xl border border-white/[0.12] bg-[rgba(14,16,22,0.94)] p-2 shadow-2xl backdrop-blur-2xl">
            <ul className="flex flex-col">
              {sites.map((s) => (
                <li key={s.site_id} className="rounded-lg px-2 py-1.5">
                  <div className="flex items-center gap-2">
                    <span
                      className={`size-1.5 shrink-0 rounded-full ${
                        s.phase === "searching"
                          ? "animate-pulse bg-[var(--accent)]"
                          : s.phase === "error"
                            ? "bg-[#ff6b6b]"
                            : "bg-[#4ade80]"
                      }`}
                    />
                    <span className="min-w-0 flex-1 truncate text-[12px] text-[var(--text)]">
                      {s.site_name}
                    </span>
                    {/* 右侧固定：状态 + 条数/耗时。失败也带耗时——超时一眼可辨 */}
                    {s.phase === "searching" && (
                      <span className="shrink-0 text-[11px] text-[var(--text-faint)]">
                        搜索中…
                      </span>
                    )}
                    {s.phase === "ok" && (
                      <span className="tnum shrink-0 text-[11px] text-[var(--text-muted)]">
                        {s.count} 条
                        {s.elapsed_ms !== null && (
                          <span className="text-[var(--text-faint)]">
                            {" "}· {formatElapsed(s.elapsed_ms)}
                          </span>
                        )}
                      </span>
                    )}
                    {s.phase === "error" && (
                      <span className="tnum shrink-0 text-[11px] text-[#ff9a9a]">
                        失败
                        {s.elapsed_ms !== null && (
                          <span className="opacity-75"> · {formatElapsed(s.elapsed_ms)}</span>
                        )}
                      </span>
                    )}
                  </div>
                  {/* 失败原因独立成行，不再和站名挤在一行里被截断到没法读 */}
                  {s.phase === "error" && s.error && (
                    <p
                      title={s.error}
                      className="mt-0.5 line-clamp-2 pl-3.5 text-[11px] leading-4 text-[#ff9a9a]/80"
                    >
                      {s.error}
                    </p>
                  )}
                </li>
              ))}
            </ul>
            {/* 汇总行：整次搜索的总耗时（≈ 最慢站点耗时）；快照回放同样有值 */}
            {totalElapsedMs !== null && (
              <div className="mt-1 border-t border-white/[0.08] px-2 pb-0.5 pt-1.5 text-[11px] text-[var(--text-faint)]">
                总耗时 {formatElapsed(totalElapsedMs)}
                <span className="opacity-70">（以最慢的站点为准）</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/* —— 图览模式：海报墙 + 无图结果回退列表 —— */

/**
 * 图览模式的结果主体：带海报的结果渲染成海报网格，没有海报的（多数站点
 * 不返回 poster_url）仍按普通列表排在其后——两种视图共享同一套筛选与排序。
 * 全部结果都无海报时不渲染空网格，整页自然退化为普通列表。
 */
function PosterResults({ hits }: { hits: TorrentHit[] }) {
  const withPoster = hits.filter((h) => h.poster_url);
  const withoutPoster = hits.filter((h) => !h.poster_url);
  return (
    <div className="space-y-5">
      {withPoster.length > 0 && (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3">
          {withPoster.map((hit) => (
            <TorrentPosterCard key={`${hit.site_id}:${hit.torrent_id}`} hit={hit} />
          ))}
        </ul>
      )}
      {withoutPoster.length > 0 && (
        <div>
          {withPoster.length > 0 && (
            <p className="text-on-image mb-2 text-[11px] text-[rgba(243,245,249,0.7)]">
              以下 {withoutPoster.length} 条结果没有海报，按列表展示
            </p>
          )}
          <ul className="space-y-2">
            {withoutPoster.map((hit) => (
              <TorrentRow key={`${hit.site_id}:${hit.torrent_id}`} hit={hit} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

/**
 * 种子海报卡片：2:3 竖版海报 + 底部渐变上的标题/来源，hover 浮出详情/下载。
 * 与媒体条目的 PosterCard（components/poster-card.tsx）是两套东西——这里的数据
 * 是 TorrentHit，徽章是促销信息，点击弹多图灯箱而非进详情页，故不共用卡片层，
 * 仅通过 PosterImage 共用海报图片底座（懒加载 / no-referrer / 失败回退）。
 * 点击卡片弹出多图灯箱（海报 + image_urls 里的截图等），多图时右上角
 * 显示张数徽标；hover 上的详情/下载链接 stopPropagation，不触发灯箱。
 */
/**
 * 海报卡片左上角的促销徽标（免费 / 折扣 / 双倍上传 / H&R）。
 *
 * 与列表模式的 PromoBadges 同一判断口径，但样式为海报适配：用较实的底色 +
 * backdrop-blur，保证叠在明亮海报上依然清晰；免费最醒目（实绿底深字），
 * 因为「是否免费」是海报模式下用户最关心的下载决策依据。
 */
function posterPromoBadges(hit: TorrentHit): { text: string; cls: string }[] {
  const badges: { text: string; cls: string }[] = [];
  if (hit.free || hit.download_volume_factor === 0) {
    badges.push({ text: "免费", cls: "bg-[#4ade80]/90 text-[#052e16]" });
  } else if (hit.download_volume_factor < 1) {
    badges.push({
      text: `${Math.round(hit.download_volume_factor * 100)}%`,
      cls: "bg-[#6aa7ff]/85 text-white",
    });
  }
  if (hit.upload_volume_factor > 1) {
    badges.push({ text: `${hit.upload_volume_factor}× 上传`, cls: "bg-[#c792ff]/85 text-white" });
  }
  if (hit.hit_and_run) {
    badges.push({ text: "H&R", cls: "bg-[#f59e0b]/90 text-[#3a2600]" });
  }
  return badges;
}

function TorrentPosterCard({ hit }: { hit: TorrentHit }) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const size = hit.size ?? formatBytes(hit.size_bytes);
  const name = parsedName(hit);
  // 灯箱图集：海报 + 全部图片（poster_url 通常是 image_urls 第一张，去重兜底）
  const gallery = Array.from(
    new Set([hit.poster_url, ...hit.image_urls].filter((u): u is string => !!u)),
  ).map(cachedImageUrl);
  return (
    <li className="group relative overflow-hidden rounded-xl border border-white/[0.08] bg-[rgba(14,16,22,0.5)] transition-colors hover:border-white/[0.2]">
      <div
        role="button"
        tabIndex={0}
        // 底部标题展示解析片名后，原始种子名/副标题靠这里的悬停提示查看
        // （底部渐变层是 pointer-events-none，title 只能挂在这个容器上）
        title={rawTitleTooltip(hit)}
        aria-label={`浏览「${hit.title}」的 ${gallery.length} 张图片`}
        onClick={() => setViewerOpen(true)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            setViewerOpen(true);
          }
        }}
        className="relative aspect-[2/3] cursor-zoom-in outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]"
      >
        <PosterImage
          src={hit.poster_url ? cachedImageUrl(hit.poster_url) : undefined}
          alt={hit.title}
          className="absolute inset-0 size-full transition-transform duration-500 ease-out group-hover:scale-[1.04]"
          fallback={
            // 占位：海报缺失/加载失败时的深色底 + 居中站点名
            <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-b from-white/[0.05] to-black/40">
              <span className="px-3 text-center text-[11px] text-[var(--text-faint)]">
                {hit.site_name}
              </span>
            </div>
          }
        />
        {/* 顶部徽标：左上留给促销（免费最醒目，是下载与否的决策关键），右上图片张数。
            站点名不占这个最显眼的位置——它是次要的来源信息，下沉到底部弱化展示。 */}
        <div className="absolute inset-x-1.5 top-1.5 flex items-start justify-between gap-1">
          <span className="flex flex-col items-start gap-1">
            {posterPromoBadges(hit).map((b) => (
              <span
                key={b.text}
                className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold backdrop-blur-sm ${b.cls}`}
              >
                {b.text}
              </span>
            ))}
          </span>
          {/* 张数常显（含 1 张）：点开前就知道里面有几张图，只有一张时不必特意点开 */}
          <span
            title={`共 ${gallery.length} 张图片，点击卡片浏览`}
            className="tnum flex shrink-0 items-center gap-1 rounded-md bg-black/55 px-1.5 py-0.5 text-[10px] font-medium text-white/85 backdrop-blur-sm"
          >
            <PhotoIcon className="size-3" />
            {gallery.length}
          </span>
        </div>
        {/* 底部渐变 + 标题/元信息 */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/85 via-black/45 to-transparent px-2.5 pb-2 pt-8">
          <p className="line-clamp-2 text-[12px] font-medium leading-4 text-white">
            {name ? name.primary : hit.title}
            {name && hit.attrs?.year != null && (
              <span className="tnum ml-1.5 font-normal text-white/60">{hit.attrs.year}</span>
            )}
          </p>
          <p className="tnum mt-1 text-[10px] text-white/70">
            {size && <span className="mr-2">{size}</span>}
            <span className="text-[#a7d9b6]">↑{hit.seeders}</span>
            <span className="ml-1.5 text-[#e0c19f]">↓{hit.leechers}</span>
          </p>
          {/* 来源站点 + 发布时间：都是次要信息，同一行弱化展示。时间相对展示，
              与列表模式统一（父级 pointer-events-none 让精确时刻的 title 失效）。 */}
          <p className="mt-0.5 flex items-center gap-1.5 text-[10px] text-white/50">
            <span className="min-w-0 truncate">{hit.site_name}</span>
            {hit.upload_time && (
              <>
                <span className="shrink-0 opacity-60">·</span>
                <span className="tnum shrink-0">{formatRelativeTime(hit.upload_time)}</span>
              </>
            )}
          </p>
        </div>
        {/* hover：压暗 + 浮出操作（stopPropagation：点链接不触发灯箱） */}
        {(hit.detail_url || hit.download_url) && (
          <div className="absolute inset-0 flex items-center justify-center gap-2 bg-black/0 opacity-0 transition duration-200 group-hover:bg-black/35 group-hover:opacity-100">
            {hit.detail_url && (
              <a
                href={hit.detail_url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="rounded-lg border border-white/40 bg-black/40 px-2.5 py-1 text-[11px] text-white backdrop-blur-sm transition-colors hover:bg-black/60"
              >
                详情
              </a>
            )}
            <DownloadButton
              hit={hit}
              className="btn-accent rounded-lg px-2.5 py-1 text-[11px] font-medium"
            />
          </div>
        )}
      </div>

      {viewerOpen && (
        <ImageLightbox
          images={gallery}
          title={hit.title}
          onClose={() => setViewerOpen(false)}
        />
      )}
    </li>
  );
}

/* —— 单条种子 —— */

/** 下载按钮的提交状态：终态（done/exists）不可再点，error 可点重试。 */
type DownloadState = "idle" | "submitting" | "done" | "exists" | "error";

const DOWNLOAD_LABEL: Record<DownloadState, string> = {
  idle: "下载",
  submitting: "提交中…",
  done: "已提交",
  exists: "已在下载器",
  error: "失败·重试",
};

/**
 * 下载按钮：把该种子提交到默认下载器。后端带站点登录态取回 .torrent。
 * 种子解析出了实体身份（类型+片名+年份）时，保存目录改为该类型**默认媒体库**
 * 的规范路径（主根/标题 (年份)）——文件落盘后媒体库的实时监控会自动识别入账；
 * 没有可靠身份则维持旧行为（下载器默认目录），不拿猜测污染库目录。
 * 结果就地反馈在按钮文字上，不弹窗打断浏览；失败可悬停看原因、点击重试。
 */
function DownloadButton({ hit, className }: { hit: TorrentHit; className: string }) {
  const [state, setState] = useState<DownloadState>("idle");
  const [error, setError] = useState<string | null>(null);
  if (!hit.download_url) return null;

  const settled = state === "done" || state === "exists";

  async function submit(e: React.MouseEvent) {
    e.stopPropagation();
    if (state === "submitting" || settled || !hit.download_url) return;
    setState("submitting");
    setError(null);
    try {
      // 实体身份三件套齐全才入库（年份是防错挂的硬门槛）
      const attrs = hit.attrs;
      const title = attrs?.titles_zh?.[0] ?? attrs?.titles_en?.[0];
      const mediaType =
        attrs?.media_type === "movie" || attrs?.media_type === "tv"
          ? attrs.media_type
          : null;
      const library =
        title && attrs?.year != null ? await defaultLibraryFor(mediaType) : null;
      const result = await submitTorrentDownload({
        site_id: hit.site_id,
        download_url: hit.download_url,
        ...(library && title
          ? {
              library_id: library.id,
              title,
              year: attrs?.year ?? null,
              subtitle: hit.subtitle || null,
            }
          : {}),
      });
      setState(result.already_exists ? "exists" : "done");
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败，请重试");
      setState("error");
    }
  }

  return (
    <button
      type="button"
      onClick={submit}
      disabled={state === "submitting"}
      title={error ?? undefined}
      className={`${className}${settled ? " cursor-default opacity-75" : ""}${
        state === "submitting" ? " cursor-wait opacity-75" : ""
      }`}
    >
      {DOWNLOAD_LABEL[state]}
    </button>
  );
}

/**
 * 做种数的健康度着色：让"这个种还活不活跃"变成扫一眼即得的预注意特征，
 * 不用逐行读数字。0=死种警示红，<5=濒危琥珀，≥100=充沛绿加粗，其余常规绿。
 */
function seederTone(n: number): string {
  if (n === 0) return "text-[#ff9a9a]";
  if (n < 5) return "text-[#fbbf24]";
  if (n >= 100) return "font-semibold text-[#4ade80]";
  return "text-[#79d193]";
}

/**
 * 解析展示名：优先小模型抽取的片名，中文主名在前、外文主名做次级标注；
 * 只有外文名时它就是主名。两者都提取不到返回 null，调用方回退原始种子名。
 */
function parsedName(hit: TorrentHit): { primary: string; secondary: string | null } | null {
  const zh = hit.attrs?.titles_zh?.[0] ?? null;
  const en = hit.attrs?.titles_en?.[0] ?? null;
  if (zh) return { primary: zh, secondary: en };
  if (en) return { primary: en, secondary: null };
  return null;
}

/** 悬停提示：原始种子名 + 站点副标题（片名展示成解析结果后，原始信息从这里看）。 */
function rawTitleTooltip(hit: TorrentHit): string {
  return [hit.title, hit.subtitle].filter(Boolean).join("\n");
}

/**
 * 版本规格摘要（分组模式的行标题）：片名已由组头承担，行内只描述这个版本
 * 与同组其他版本的差异——季集 + 分辨率 + 片源/Remux + 编码 + HDR + 音轨 + 压制组。
 */
function specSummary(attrs: TorrentAttrs): string | null {
  const parts = [
    seasonEpLabel(attrs),
    attrs.resolution,
    attrs.media_source,
    attrs.remux && "Remux",
    attrs.video_codec,
    ...attrs.hdr,
    ...attrs.audio.slice(0, 2),
    attrs.release_group,
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : null;
}

/**
 * 行布局采用「左弹性标题区 + 右固定数字网格 + 操作区」三段式：
 * 体积/做种/完成/时间锁定在固定宽度的 2×2 网格里右对齐，跨行位置完全一致——
 * 眼睛沿一条垂直线扫下来即可完成全列表对比（Kayak/qBittorrent 式扫描列），
 * 不再混在自由流动的 meta 文本里逐行找数字。
 */
function TorrentRow({
  hit,
  grouped = false,
  showRawTitles = false,
}: {
  hit: TorrentHit;
  grouped?: boolean;
  /** 列表模式直接展示站点原始种子名与副标题，不使用扩充层解析出的片名。 */
  showRawTitles?: boolean;
}) {
  const size = hit.size ?? formatBytes(hit.size_bytes);
  const name = showRawTitles ? null : parsedName(hit);
  // 分组模式且片名已解析：片名由组头承担，行标题换成版本规格摘要
  const asSpecRow = grouped && name !== null;
  const spec = asSpecRow && hit.attrs ? specSummary(hit.attrs) : null;
  return (
    <li className="group relative rounded-2xl border border-white/[0.06] bg-[rgba(14,16,22,0.42)] px-4 py-3.5 backdrop-blur-xl transition-all hover:-translate-y-px hover:border-white/[0.13] hover:bg-[rgba(20,23,31,0.58)] hover:shadow-[0_12px_30px_-18px_rgba(0,0,0,0.8)]">
      <div className="flex items-center gap-5">
        {/* 标题优先，来源和属性下沉为辅助信息，避免徽标抢走首屏注意力。 */}
        <div className="min-w-0 flex-1">
          {asSpecRow ? (
            <p
              title={rawTitleTooltip(hit)}
              className="truncate text-[13px] font-medium leading-5 text-[var(--text)]"
            >
              {spec ?? hit.title}
            </p>
          ) : name ? (
            // 解析出片名：标题行 = 主名 + 外文名 + 年份，原始种子名/副标题收进悬停提示
            <p
              title={rawTitleTooltip(hit)}
              className="truncate text-[13.5px] font-medium leading-5 text-[var(--text)]"
            >
              {name.primary}
              {name.secondary && (
                <span className="ml-2 text-[12px] font-normal text-[var(--text-muted)]">
                  {name.secondary}
                </span>
              )}
              {hit.attrs?.year != null && (
                <span className="tnum ml-2 text-[12px] font-normal text-[var(--text-faint)]">
                  {hit.attrs.year}
                </span>
              )}
            </p>
          ) : (
            <p className="truncate text-[13.5px] font-medium leading-5 text-[var(--text)]">
              {hit.title}
            </p>
          )}
          {((!name && hit.subtitle) || hit.site_category_name) && (
            <p className="mt-0.5 flex items-baseline gap-2 text-[12px] text-[var(--text-muted)]">
              {!name && hit.subtitle && (
                <span className="min-w-0 truncate">{hit.subtitle}</span>
              )}
              {hit.site_category_name && (
                <span className="shrink-0 text-[11px] text-[var(--text-faint)]">
                  {hit.site_category_name}
                </span>
              )}
            </p>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <span className="shrink-0 rounded-md bg-white/[0.06] px-1.5 py-0.5 text-[10px] font-medium text-[var(--accent-2)]">
              {hit.site_name}
            </span>
            <PromoBadges hit={hit} />
            {/* 规格行模式下不再重复属性徽标（规格已在行标题里） */}
            {hit.attrs && !asSpecRow && <AttrBadges attrs={hit.attrs} />}
          </div>
        </div>

        {/* 固定指标列带短标签，数值的含义无需靠图标猜测，跨行仍可垂直比较。 */}
        <div className="tnum hidden shrink-0 grid-cols-[70px_78px_78px] gap-x-3 text-right text-[11px] lg:grid">
          <Metric label="大小" value={size || "—"} />
          <Metric label="做种 / 下载" value={<><span className={seederTone(hit.seeders)}>{hit.seeders}</span><span className="text-[var(--text-faint)]"> / {hit.leechers}</span></>} />
          <Metric
            label={hit.snatched > 0 ? `完成 ${hit.snatched}` : "发布时间"}
            value={hit.upload_time ? formatRelativeTime(hit.upload_time) : "—"}
            title={hit.upload_time ? formatDateTime(hit.upload_time) : undefined}
          />
        </div>

        {/* 操作区默认收起，hover 整行或键盘聚焦时再浮现：列表静止时专注内容，
            同时保留 focus-within，确保键盘用户可以访问操作。渐变遮罩覆盖下方指标列，
            避免按钮出现时文字相互叠压。 */}
        {(hit.detail_url || hit.download_url) && (
          <div className="pointer-events-none absolute inset-y-0 right-2 flex items-center gap-1.5 rounded-r-2xl bg-gradient-to-l from-[rgba(20,23,31,0.98)] from-65% to-transparent pl-16 pr-2 opacity-0 transition-opacity duration-150 group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
            {hit.detail_url && (
              <a
                href={hit.detail_url}
                target="_blank"
                rel="noreferrer"
                className="btn-glass h-7 px-3 text-[11px] text-[var(--text-muted)]"
              >
                详情
              </a>
            )}
            <DownloadButton
              hit={hit}
              className="btn-accent flex h-7 items-center rounded-full px-3 text-[11px] font-medium"
            />
          </div>
        )}
      </div>
    </li>
  );
}

/** 列表指标单元：固定标签 + 数值的两级层次，让密集数字仍然可快速扫读。 */
function Metric({
  label,
  value,
  title,
}: {
  label: string;
  value: React.ReactNode;
  title?: string;
}) {
  return (
    <span title={title} className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate text-[9px] text-[var(--text-faint)]">{label}</span>
      <span className="truncate text-[var(--text-muted)]">{value}</span>
    </span>
  );
}

/** 促销角标：免费 / 打折下载系数 / 双倍上传 / H&R 考核。 */
function PromoBadges({ hit }: { hit: TorrentHit }) {
  const badges: { text: string; cls: string }[] = [];
  if (hit.free || hit.download_volume_factor === 0) {
    badges.push({ text: "免费", cls: "bg-[#4ade80]/15 text-[#79d193]" });
  } else if (hit.download_volume_factor < 1) {
    badges.push({
      text: `${Math.round(hit.download_volume_factor * 100)}%`,
      cls: "bg-[#6aa7ff]/15 text-[#9cc2ff]",
    });
  }
  if (hit.upload_volume_factor > 1) {
    badges.push({
      text: `${hit.upload_volume_factor}× 上传`,
      cls: "bg-[#c792ff]/15 text-[#c8a6ff]",
    });
  }
  if (hit.hit_and_run) {
    badges.push({ text: "H&R", cls: "bg-[#f59e0b]/15 text-[#fbbf24]" });
  }
  return (
    <>
      {badges.map((b) => (
        <span
          key={b.text}
          className={`rounded-md px-1.5 py-0.5 text-[10px] font-semibold ${b.cls}`}
        >
          {b.text}
        </span>
      ))}
    </>
  );
}

/** 每行常显的属性徽标上限；超出部分折叠成 +N（悬停看全部）。 */
const MAX_ATTR_BADGES = 4;

/**
 * 扩充属性徽标（降噪版）：按信息量优先级取前 4 个常显，其余折叠成 +N。
 *
 * 等权重小徽标超过 4-5 个后可读性反而下降（chip fatigue），且分辨率/片源/编码
 * 这些维度在顶部筛选栏已可见可筛。优先级：季集（剧集的关键标识）> 分辨率 >
 * Remux > HDR > 压制组（PT 用户在意）> 片源 > 编码 > 音频。
 * 影视类型不再入列——顶部的类型分段切换已表达同一信息，每行重复是纯冗余。
 * 标题串（release name）本身也完整携带这些属性，+N 悬停即见全部。
 */
/** 题材轴的中文标签（attrs.content_type → 徽标文案）。 */
const CONTENT_TYPE_LABEL: Record<string, string> = {
  anime: "动漫",
  documentary: "纪录片",
  variety: "综艺",
  music: "音乐",
};

function AttrBadges({ attrs }: { attrs: TorrentAttrs }) {
  const chips: { text: string; cls?: string }[] = [];
  const seasonEp = seasonEpLabel(attrs);
  if (seasonEp) chips.push({ text: seasonEp, cls: "text-[var(--accent-2)]" });
  if (attrs.content_type && CONTENT_TYPE_LABEL[attrs.content_type]) {
    chips.push({ text: CONTENT_TYPE_LABEL[attrs.content_type], cls: "text-[#f0b6d8]" });
  }
  if (attrs.resolution) chips.push({ text: attrs.resolution });
  if (attrs.remux) chips.push({ text: "Remux", cls: "text-[#9cc2ff]" });
  for (const v of attrs.hdr) chips.push({ text: v, cls: "text-[#c8a6ff]" });
  if (attrs.release_group) chips.push({ text: attrs.release_group, cls: "text-[var(--accent)]" });
  if (attrs.media_source) chips.push({ text: attrs.media_source });
  if (attrs.video_codec) chips.push({ text: attrs.video_codec });
  for (const v of attrs.audio.slice(0, 2)) chips.push({ text: v });
  if (!chips.length) return null;

  const shown = chips.slice(0, MAX_ATTR_BADGES);
  const folded = chips.slice(MAX_ATTR_BADGES);
  return (
    <>
      {shown.map((c) => (
        <span
          key={c.text}
          className={`rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] ${c.cls ?? "text-[var(--text-muted)]"}`}
        >
          {c.text}
        </span>
      ))}
      {folded.length > 0 && (
        <span
          title={folded.map((c) => c.text).join(" · ")}
          className="tnum cursor-default rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10px] text-[var(--text-faint)]"
        >
          +{folded.length}
        </span>
      )}
    </>
  );
}

/**
 * 季集摘要："S02E03"、"S01-S05"、"E19-E20"、"全集"/"全12集"。
 * 只描述观测值：seasons/episodes 为空时不硬造（complete 单独成词，
 * 观测到总集数时进一步写成"全N集"）。
 */
function seasonEpLabel(attrs: TorrentAttrs): string | null {
  const pad = (n: number) => String(n).padStart(2, "0");
  const range = (nums: number[], prefix: string) =>
    nums.length === 1
      ? `${prefix}${pad(nums[0])}`
      : `${prefix}${pad(nums[0])}-${prefix}${pad(nums[nums.length - 1])}`;

  const parts: string[] = [];
  if (attrs.seasons.length === 1 && attrs.episodes.length) {
    parts.push(`S${pad(attrs.seasons[0])}${range(attrs.episodes, "E")}`);
  } else {
    if (attrs.seasons.length) parts.push(range(attrs.seasons, "S"));
    if (attrs.episodes.length && !attrs.complete) parts.push(range(attrs.episodes, "E"));
  }
  if (attrs.complete) {
    parts.push(attrs.episodes_total ? `全${attrs.episodes_total}集` : "全集");
  }
  return parts.length ? parts.join(" ") : null;
}

/* —— 占位 / 空态 —— */

/**
 * 页头底部的搜索进度条：start 事件未到（不知道站点数）时是来回扫光的
 * 不定进度，之后按「已完成站点 / 总站点」比例填充；完成后随 header 收起。
 */
function SearchProgressBar({ settled, total }: { settled: number; total: number }) {
  return (
    <div className="relative mt-3 h-[2px] overflow-hidden rounded-full bg-white/[0.07]">
      {total === 0 ? (
        <span className="progress-sweep absolute inset-y-0 w-2/5 rounded-full bg-gradient-to-r from-transparent via-[var(--accent)] to-transparent" />
      ) : (
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-gradient-to-r from-[var(--accent)] to-[var(--accent-2)] transition-[width] duration-500 ease-out"
          // 起步给 6% 的可见进度：0 个站点完成时进度条也不该是空的，用户要能看到"已经开始了"
          style={{ width: `${Math.max(6, (settled / total) * 100)}%` }}
        />
      )}
    </div>
  );
}

/** 骨架标题条的宽度序列：错落模拟真实标题长短不一，行数也由它决定。 */
const SKELETON_TITLE_WIDTHS = ["72%", "58%", "80%", "64%", "70%", "52%"];

/**
 * 首批结果到达前的等待态：状态行（旋转指示 + 正在搜索几个站点）+ 模拟结果行
 * 结构的 shimmer 骨架（徽标排 / 标题条 / 元信息条），逐行错峰扫光、透明度递减。
 * 结果是流式渐进的，通常最快的站点亚秒级就会替换掉这里。
 */
function SkeletonList({ siteCount }: { siteCount: number }) {
  return (
    <div>
      <div className="text-on-image flex items-center gap-2 px-1 pb-3 text-[12.5px] text-[rgba(243,245,249,0.8)]">
        <svg
          viewBox="0 0 24 24"
          className="size-3.5 animate-spin text-[var(--accent)]"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M12 3a9 9 0 1 1-9 9" />
        </svg>
        {siteCount > 0
          ? `正在搜索 ${siteCount} 个站点，结果将实时呈现…`
          : "正在连接搜索服务…"}
      </div>
      <ul className="space-y-2" aria-hidden="true">
        {SKELETON_TITLE_WIDTHS.map((width, i) => (
          <li
            key={i}
            className="rounded-2xl border border-white/[0.05] bg-[rgba(14,16,22,0.35)] px-4 py-3 backdrop-blur-xl"
            style={{ opacity: 1 - i * 0.13, "--stagger": `${i * 120}ms` } as React.CSSProperties}
          >
            {/* 徽标排：站点名 + 属性小徽标 */}
            <div className="flex items-center gap-1.5">
              <span className="skeleton-block h-[17px] w-14 rounded-md" />
              <span className="skeleton-block h-[17px] w-10 rounded-md" />
              <span className="skeleton-block h-[17px] w-12 rounded-md" />
            </div>
            {/* 标题条 */}
            <div className="skeleton-block mt-2.5 h-[13px] rounded-md" style={{ width }} />
            {/* 元信息条：体积 / 做种 / 下载 / 日期 */}
            <div className="mt-2.5 flex items-center gap-3">
              <span className="skeleton-block h-[10px] w-14 rounded" />
              <span className="skeleton-block h-[10px] w-9 rounded" />
              <span className="skeleton-block h-[10px] w-9 rounded" />
              <span className="skeleton-block h-[10px] w-16 rounded" />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

function EmptyHint({ title, hint }: { title: string; hint: string }) {
  return (
    <div className="flex min-h-[240px] flex-col items-center justify-center text-center">
      <p className="text-on-image text-[15px] font-medium text-white">{title}</p>
      <p className="text-on-image mt-1.5 max-w-sm text-[13px] leading-6 text-[rgba(243,245,249,0.82)]">
        {hint}
      </p>
    </div>
  );
}

/* —— 工具函数 —— */
function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}
