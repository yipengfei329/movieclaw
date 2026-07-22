"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { Route } from "next";
import Link from "next/link";
import { createPortal } from "react-dom";

import { DirectoryPicker } from "@/components/directory-picker";
import {
  DownloadIcon,
  FilmIcon,
  FolderIcon,
  MoreIcon,
  PlusIcon,
  TvIcon,
  XIcon,
} from "@/components/icons";
import { LibraryOrganizeDialog } from "@/components/library-organize-dialog";
import { MediaRow } from "@/components/media-row";
import type { PosterCardAction } from "@/components/poster-card";
import { Tooltip } from "@/components/tooltip";
import {
  type IngestDir,
  type LibraryItem,
  type LibraryPayload,
  type MediaLibrary,
  type ScanProgress,
  createLibrary,
  deleteLibrary,
  listLibraries,
  listLibraryItems,
  setDefaultLibrary,
  startLibraryScan,
  updateLibrary,
} from "@/lib/api/libraries";
import type { Subscription } from "@/lib/api/subscriptions";
import { formatBytes } from "@/lib/format";
import { cachedImageUrl } from "@/lib/image-proxy";
import type { MediaItem, MediaType } from "@/lib/media-types";

/** 库类型 → 展示名与图标 */
export const LIBRARY_KIND_META: Record<MediaType, { label: string; Icon: typeof FilmIcon }> = {
  movie: { label: "电影", Icon: FilmIcon },
  tv: { label: "剧集", Icon: TvIcon },
};

/**
 * 库存条目的悬浮操作三分支（库首页「最近添加」行与单库库存墙共用）：
 *   - 在播剧 → 订阅追新（还会有新集，转化价值最高；已订阅时卡片自动显「已订阅」）；
 *   - 完结剧且已播集有缺口 → 补齐缺集（整季没下过的内容不在文件台账里，
 *     「缺失重下」够不着，建订阅是唯一补齐路径；订阅按 E−H 只补缺的集）；
 *   - 其余（电影 / 完结齐全 / 播出状态未知）→ 静态「已入库」标识，不给死按钮。
 */
export function libraryCardAction(item: LibraryItem): PosterCardAction {
  if (item.kind !== "tv") return "owned";
  if (item.air_status === "airing") return "follow";
  if (item.air_status === "ended" && item.missing_episode_count > 0) return "backfill";
  return "owned";
}

/**
 * 订阅的实际归属库：显式指定优先，否则该类型的默认库。
 * 与后端 resolve_for_subscription 同一语义，库页与单库页共用。
 */
export function effectiveLibraryId(
  sub: Subscription,
  libraries: MediaLibrary[],
): number | null {
  if (sub.library_id != null) return sub.library_id;
  return libraries.find((l) => l.kind === sub.media.kind && l.is_default)?.id ?? null;
}

/**
 * 媒体库页（/library）：全部库的 Emby 风格卡片墙。
 *
 * 每张卡是一个库：封面用库内作品的海报做「货架」展示（最多 4 张站立海报
 * 带底部倒影，纯前端 CSS 合成、零后端开销），叠库名/类型/统计；
 * 点击进入单库海报墙（/library/[id]）。库的增删改/设默认/扫描都在本页
 * 完成——媒体库是内容的一等入口，不是配置项。
 *
 * 数据源是 library_file 台账的**真实库存**（L3 起）：入库管线与存量扫描
 * 落账的文件聚合，不再用订阅占位。
 */
export function LibraryView() {
  const [libraries, setLibraries] = useState<MediaLibrary[] | null>(null);
  const [itemsByLibrary, setItemsByLibrary] = useState<Map<number, LibraryItem[]>>(
    new Map(),
  );
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 弹窗态：新增（"new"）/ 编辑（库对象）/ 关闭(null)
  const [editing, setEditing] = useState<MediaLibrary | "new" | null>(null);
  // 整理文件名对话框的目标库；null = 关闭
  const [organizeTarget, setOrganizeTarget] = useState<MediaLibrary | null>(null);

  const reload = useCallback(() => {
    setFailed(false);
    listLibraries()
      .then(async (libs) => {
        setLibraries(libs);
        // 封面拼图需要各库的条目海报；库的数量级很小，并发拉取即可
        const entries = await Promise.all(
          libs.map(
            async (lib) => [lib.id, await listLibraryItems(lib.id).catch(() => [])] as const,
          ),
        );
        setItemsByLibrary(new Map(entries));
      })
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  // 有库在扫描/整理时轮询刷新，任务完成即看到最新库存与文件名
  const busyAny = (libraries ?? []).some((l) => l.scanning || l.organizing);
  useEffect(() => {
    if (!busyAny) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [busyAny, reload]);

  // 每个非空库一行「最近添加」：按最近入账时间倒序取前 20，复用发现页的
  // 横滚海报行；悬浮动作按条目三分支（追新/补齐/已入库，见 libraryCardAction）
  const recentRows = useMemo(
    () =>
      (libraries ?? [])
        .map((library) => {
          const recent = [...(itemsByLibrary.get(library.id) ?? [])]
            .sort((a, b) => (b.added_at ?? "").localeCompare(a.added_at ?? ""))
            .slice(0, 20);
          // MediaItem.id 即 tmdb_id 字符串，库类型固定故同行内唯一，可作动作映射键
          const actions = new Map(
            recent.map((it) => [String(it.tmdb_id), libraryCardAction(it)]),
          );
          return {
            library,
            items: recent.map(libraryItemToMediaItem),
            actionOf: (m: MediaItem) => actions.get(m.id) ?? ("owned" as const),
          };
        })
        .filter((row) => row.items.length > 0),
    [libraries, itemsByLibrary],
  );

  return (
    <div className="scroll-thin flex-1 overflow-y-auto pb-10">
      <div className="flex items-start justify-between gap-4 px-6 pt-2">
        <div>
          <h2 className="text-on-image text-[26px] font-bold leading-tight tracking-[-0.02em] text-white">
            媒体库
          </h2>
          <p className="text-on-image mt-1.5 text-[13px] text-[var(--text-muted)]">
            你的影视收藏在这里安家：订阅与下载的内容按「入库到哪个库」落盘，Plex / Emby 可直接识别
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="btn-accent flex shrink-0 items-center gap-1 rounded-full py-2 pl-3 pr-4 text-[13px] font-semibold"
        >
          <PlusIcon className="size-4" />
          添加媒体库
        </button>
      </div>

      {error && (
        <div className="mx-6 mt-4 rounded-xl border border-[#ff6b6b]/30 bg-[#ff6b6b]/10 px-4 py-3 text-sm text-[#ff6b6b]">
          {error}
        </div>
      )}

      {libraries === null && !failed && (
        <div className="mt-16 flex items-center justify-center gap-2.5 text-[13px] text-[var(--text-muted)]">
          <span className="size-4 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
          正在加载媒体库…
        </div>
      )}

      {failed && (
        <div className="mt-16 flex flex-col items-center gap-3 text-center">
          <p className="text-[13.5px] text-[var(--text-muted)]">媒体库加载失败</p>
          <button
            type="button"
            onClick={reload}
            className="btn-glass px-4 py-2 text-[13px] font-medium text-[var(--text)]"
          >
            重试
          </button>
        </div>
      )}

      {libraries !== null && !failed && (
        <div className="mt-6 grid gap-5 px-6 [grid-template-columns:repeat(auto-fill,minmax(230px,280px))]">
          {libraries.map((library) => (
            <LibraryCard
              key={library.id}
              library={library}
              items={itemsByLibrary.get(library.id) ?? []}
              onEdit={() => setEditing(library)}
              onOrganize={() => setOrganizeTarget(library)}
              onRefresh={reload}
              onError={setError}
            />
          ))}
        </div>
      )}

      {/* —— 最近添加：Emby 首页式分区，每个非空库一行横滚海报 —— */}
      {recentRows.length > 0 && (
        <div className="mt-10 space-y-8">
          {recentRows.map(({ library, items, actionOf }) => (
            <MediaRow
              key={library.id}
              row={{
                id: `library-recent-${library.id}`,
                title: `最近添加的${library.name}`,
                items,
              }}
              moreHref={`/library/${library.id}` as Route}
              moreLabel="查看全部"
              cardAction={actionOf}
            />
          ))}
        </div>
      )}

      <LibraryFormDialog
        state={editing}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          reload();
        }}
      />

      <LibraryOrganizeDialog
        library={organizeTarget}
        onClose={() => setOrganizeTarget(null)}
        onChanged={reload}
      />
    </div>
  );
}

/**
 * 库存条目 → 发现页海报卡的数据形态。点击走 /media/{type}/{tmdb_id} 详情
 * （与单库页库存格同一目标）；徽章位放最高清晰度，副行放规模/大小。
 */
function libraryItemToMediaItem(item: LibraryItem): MediaItem {
  let extent = "";
  if (item.kind === "tv" && item.episode_count > 0) {
    extent =
      item.seasons.length === 1
        ? `第 ${item.seasons[0]} 季 · ${item.episode_count} 集`
        : `${item.seasons.length} 季 · ${item.episode_count} 集`;
  } else if (item.kind === "movie" && item.total_size_bytes > 0) {
    extent = formatBytes(item.total_size_bytes);
  }
  return {
    id: String(item.tmdb_id),
    source: "tmdb",
    type: item.kind,
    title: item.title,
    originalTitle: "",
    year: item.year ?? 0,
    rating: 0,
    genres: [],
    extent,
    badges: item.resolutions.slice(0, 1),
    overview: "",
    posterUrl: item.poster_url ? cachedImageUrl(item.poster_url) : "",
  };
}

/* —— 库卡片：海报货架封面 + 库名/徽标/计数，Emby「我的媒体」磁贴风 —— */

function LibraryCard({
  library,
  items,
  onEdit,
  onOrganize,
  onRefresh,
  onError,
}: {
  library: MediaLibrary;
  items: LibraryItem[];
  onEdit: () => void;
  onOrganize: () => void;
  onRefresh: () => void;
  onError: (message: string) => void;
}) {
  const meta = LIBRARY_KIND_META[library.kind];
  // 封面海报取最近入库的 4 部（与下方「最近添加」行同一排序口径）
  const posters = [...items]
    .sort((a, b) => (b.added_at ?? "").localeCompare(a.added_at ?? ""))
    .map((s) => s.poster_url)
    .filter((u): u is string => Boolean(u))
    .slice(0, 4);
  const { stats } = library;
  const summary =
    stats.item_count > 0
      ? `${stats.item_count} 部作品 · ${formatBytes(stats.total_size_bytes)}`
      : "暂无内容 · 扫描或订阅入库后展示";

  return (
    <div className="group/lib relative">
      <Link
        href={`/library/${library.id}` as Route}
        aria-label={`打开「${library.name}」`}
        className="block overflow-hidden rounded-2xl ring-1 ring-white/10 outline-none transition duration-300 hover:ring-white/35 focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]"
      >
        <div className="relative aspect-[21/10] bg-[#0a0c12]">
          <LibraryCover posters={posters} Icon={meta.Icon} />
          {(library.scanning || library.organizing) && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/55 backdrop-blur-[2px]">
              <ScanProgressRing
                progress={library.scanning ? library.scan_progress : library.organize_progress}
              />
            </div>
          )}
        </div>
      </Link>

      {/* 库名/徽标/统计：Emby 式放在封面下方居中，不再叠在海报上 */}
      <div className="mt-2.5 px-2">
        <div className="flex items-center justify-center gap-2">
          <h3 className="truncate text-[15px] font-semibold text-white">{library.name}</h3>
          {library.is_default && (
            <span className="shrink-0 rounded-full border border-white/[0.14] bg-white/[0.1] px-2 py-0.5 text-[10.5px] font-semibold text-white/80">
              默认
            </span>
          )}
          {(library.scanning || library.organizing) && (
            <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-white/[0.14] bg-white/[0.06] px-2 py-0.5 text-[10.5px] font-semibold text-white/80">
              <span className="size-2.5 animate-spin rounded-full border border-white/30 border-t-white/90" />
              {library.scanning ? "扫描中" : "整理中"}
            </span>
          )}
          {stats.unidentified_count > 0 && (
            <span className="shrink-0 rounded-full border border-[#f5c451]/40 bg-[#f5c451]/15 px-2 py-0.5 text-[10.5px] font-semibold text-[#f5c451]">
              {stats.unidentified_count} 个待识别
            </span>
          )}
        </div>
        <p
          className="mt-1 truncate text-center text-[11.5px] text-white/50"
          title={library.primary_root ?? undefined}
        >
          {meta.label} · {summary}
          {library.primary_root ? ` · ${library.primary_root}` : ""}
        </p>
      </div>

      {/* 管理操作：悬停浮现在右上角（Link 外层，避免点菜单触发跳转） */}
      <LibraryCardMenu
        library={library}
        onEdit={onEdit}
        onScan={() => {
          void startLibraryScan(library.id)
            .then(onRefresh)
            .catch((e) => onError((e as Error).message));
        }}
        onOrganize={onOrganize}
        onRefresh={onRefresh}
        onError={onError}
      />
    </div>
  );
}

/**
 * 封面「氛围光货架」：首张海报重模糊后铺满做氛围光晕（每个库有自己的
 * 色调），最多 4 张海报立体站排，底部倒影直接落在氛围暗底上表达
 * 「反光地面」；0 张=类型图标底纹。卡片 21/10 比例，海报占约 2/3。
 */
/** 扫描进度环：有分母画百分比，刚起步（进度未知）转圈占位。 */
function ScanProgressRing({ progress }: { progress: ScanProgress | null }) {
  const pct =
    progress && progress.total > 0
      ? Math.min(100, Math.round((progress.processed / progress.total) * 100))
      : null;
  const R = 26;
  const C = 2 * Math.PI * R;
  return (
    <div className="relative size-[72px]">
      <svg
        viewBox="0 0 64 64"
        className={`size-full -rotate-90 ${pct === null ? "animate-spin" : ""}`}
      >
        <circle cx="32" cy="32" r={R} fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="5" />
        <circle
          cx="32"
          cy="32"
          r={R}
          fill="none"
          stroke="white"
          strokeWidth="5"
          strokeLinecap="round"
          strokeDasharray={C}
          strokeDashoffset={pct === null ? C * 0.75 : C * (1 - pct / 100)}
          className="transition-[stroke-dashoffset] duration-500 ease-out"
        />
      </svg>
      <span className="absolute inset-0 flex items-center justify-center text-[13px] font-semibold text-white">
        {pct === null ? "…" : `${pct}%`}
      </span>
    </div>
  );
}

function LibraryCover({ posters, Icon }: { posters: string[]; Icon: typeof FilmIcon }) {
  if (posters.length === 0) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-[#1c2230] to-[#10131c]">
        <Icon className="size-12 text-white/[0.13]" />
      </div>
    );
  }
  return (
    <div className="absolute inset-0 overflow-hidden">
      {/* 氛围光：首图放大重模糊 + 提饱和，再整体压暗保证前景对比度 */}
      <img
        src={cachedImageUrl(posters[0])}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        className="absolute inset-0 size-full scale-150 object-cover opacity-70 blur-3xl saturate-150"
      />
      <div className="absolute inset-0 bg-[#080a10]/50" />
      {/* 灯箱底光：首图模糊后以 screen 混合从底边向上发光，颜色天然
          取自海报主色；再叠一个中性地面光斑，像射灯打在舞台地面上 */}
      <img
        src={cachedImageUrl(posters[0])}
        alt=""
        aria-hidden
        loading="lazy"
        referrerPolicy="no-referrer"
        className="absolute inset-x-0 bottom-0 h-1/2 w-full object-cover opacity-55 blur-3xl saturate-150 mix-blend-screen [mask-image:linear-gradient(to_top,black,transparent)]"
      />
      <div className="absolute inset-x-[8%] bottom-0 h-[28%] [background:radial-gradient(60%_100%_at_50%_100%,rgba(255,255,255,0.09),transparent_70%)]" />
      {/* 海报排：立在玻璃搁板上，悬停整排轻微上浮 */}
      <div className="absolute inset-x-0 top-[4.5%] flex justify-center gap-[2%] px-[2%]">
        {posters.map((url, i) => (
          <div
            key={i}
            className="w-[22.5%] shrink-0 transition duration-300 group-hover/lib:-translate-y-1"
          >
            <img
              src={cachedImageUrl(url)}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
              className="aspect-[2/3] w-full rounded-[4px] object-cover shadow-[0_6px_18px_rgba(0,0,0,0.5)] ring-1 ring-white/20"
            />
            {/* 倒影：翻转副本贴着底边，向下快速渐隐。注意 mask 在元素本地
                坐标系生效、会跟着 scaleY(-1) 一起翻转，所以这里写 to top，
                翻转后在屏幕上才是「贴近海报处最实、向下淡出」 */}
            <img
              src={cachedImageUrl(url)}
              alt=""
              aria-hidden
              loading="lazy"
              referrerPolicy="no-referrer"
              className="mt-[2px] aspect-[2/3] w-full -scale-y-100 rounded-[4px] object-cover opacity-55 blur-[1px] [mask-image:linear-gradient(to_top,rgba(0,0,0,0.7),transparent_26%)]"
            />
          </div>
        ))}
      </div>
      {/* 悬停扫光：一道斜向柔光从左扫到右掠过倒影区（transform 过渡
          实现单次扫过，移出卡片后自动滑回原位待命） */}
      <div className="pointer-events-none absolute -left-[45%] bottom-0 h-[25%] w-[45%] -skew-x-12 bg-gradient-to-r from-transparent via-white/[0.14] to-transparent transition-transform duration-700 ease-out group-hover/lib:translate-x-[350%]" />
    </div>
  );
}

/** 卡片右上角的管理菜单（Portal 到 body，同侧栏会话菜单的处理）。 */
function LibraryCardMenu({
  library,
  onEdit,
  onScan,
  onOrganize,
  onRefresh,
  onError,
}: {
  library: MediaLibrary;
  onEdit: () => void;
  onScan: () => void;
  onOrganize: () => void;
  onRefresh: () => void;
  onError: (message: string) => void;
}) {
  const [menuPos, setMenuPos] = useState<{ left: number; top: number } | null>(null);
  const open = menuPos != null;

  useEffect(() => {
    if (!open) return;
    const close = () => setMenuPos(null);
    document.addEventListener("mousedown", close);
    document.addEventListener("scroll", close, true);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("scroll", close, true);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const guard = (fn: () => Promise<unknown>) => {
    setMenuPos(null);
    void fn()
      .then(onRefresh)
      .catch((e) => onError((e as Error).message));
  };

  return (
    <>
      <button
        type="button"
        aria-label={`管理「${library.name}」`}
        onClick={(e) => {
          if (open) {
            setMenuPos(null);
            return;
          }
          const rect = e.currentTarget.getBoundingClientRect();
          setMenuPos({ left: rect.right - 144, top: rect.bottom + 6 });
        }}
        className={`absolute right-3 top-3 flex size-8 items-center justify-center rounded-lg border border-white/[0.14] bg-black/45 text-white/90 backdrop-blur-md transition-opacity duration-200 hover:bg-black/65 ${
          open ? "opacity-100" : "opacity-0 group-hover/lib:opacity-100"
        }`}
      >
        <MoreIcon className="size-4" />
      </button>

      {open &&
        createPortal(
          <div
            onMouseDown={(e) => e.stopPropagation()}
            className="surface-raised w-36 overflow-hidden rounded-xl p-1.5"
            style={{ position: "fixed", left: menuPos.left, top: menuPos.top, zIndex: 50 }}
          >
            <button
              type="button"
              onClick={() => {
                setMenuPos(null);
                onEdit();
              }}
              className="glass-row px-2.5 py-2 text-[13px] font-medium"
            >
              编辑库
            </button>
            <button
              type="button"
              disabled={library.scanning || library.organizing}
              onClick={() => {
                setMenuPos(null);
                onScan();
              }}
              className="glass-row px-2.5 py-2 text-[13px] font-medium disabled:opacity-40"
            >
              {library.scanning ? "正在扫描…" : "扫描库"}
            </button>
            <button
              type="button"
              disabled={library.scanning || library.organizing}
              onClick={() => {
                setMenuPos(null);
                onOrganize();
              }}
              className="glass-row px-2.5 py-2 text-[13px] font-medium disabled:opacity-40"
            >
              {library.organizing ? "正在整理…" : "整理文件名"}
            </button>
            <button
              type="button"
              disabled={library.is_default}
              onClick={() => guard(() => setDefaultLibrary(library.id))}
              className="glass-row px-2.5 py-2 text-[13px] font-medium disabled:opacity-40"
            >
              设为默认库
            </button>
            <button
              type="button"
              onClick={() => {
                setMenuPos(null);
                if (
                  !window.confirm(
                    `确定删除「${library.name}」？磁盘文件不受影响，挂在它上面的订阅将回落到该类型的默认库。`,
                  )
                )
                  return;
                guard(() => deleteLibrary(library.id));
              }}
              className="glass-row px-2.5 py-2 text-[13px] font-medium !text-[var(--danger)] hover:!bg-[rgba(255,107,107,0.12)]"
            >
              删除库
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}

/* —— 新增 / 编辑库的弹窗（订阅弹层同款视觉），库页与单库页共用 —— */

export function LibraryFormDialog({
  state,
  onClose,
  onSaved,
}: {
  /** "new"=新增；库对象=编辑；null=关闭 */
  state: MediaLibrary | "new" | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const library = state === "new" ? null : state;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<MediaType>("movie");
  const [name, setName] = useState("");
  // 根路径列表：第一项为主根；通过目录选择器逐个添加
  const [roots, setRoots] = useState<string[]>([]);
  // 选择器目标："add"=追加新根；数字=更改该下标的既有根（原位替换）；null=关闭
  const [pickerTarget, setPickerTarget] = useState<"add" | number | null>(null);
  // 下载监听目录列表（独立于根路径），及其目录选择器目标（同 pickerTarget 语义）
  const [ingestDirs, setIngestDirs] = useState<IngestDir[]>([]);
  const [ingestPicker, setIngestPicker] = useState<"add" | number | null>(null);

  // 每次打开时按目标重置表单（编辑带入现值，新增清空）
  useEffect(() => {
    if (state === null) return;
    setError(null);
    setKind(library?.kind ?? "movie");
    setName(library?.name ?? "");
    setRoots(library?.root_paths ?? []);
    setPickerTarget(null);
    setIngestDirs(library?.ingest_dirs ?? []);
    setIngestPicker(null);
  }, [state, library]);

  useEffect(() => {
    if (state === null) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, onClose]);

  if (state === null) return null;

  const canSubmit = !busy && name.trim().length > 0 && roots.length > 0;

  const submit = () => {
    setBusy(true);
    setError(null);
    const payload: LibraryPayload = {
      name: name.trim(),
      kind,
      root_paths: roots,
      ingest_dirs: ingestDirs,
    };
    void (library ? updateLibrary(library.id, payload) : createLibrary(payload))
      .then(onSaved)
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  const inputClass =
    "w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] " +
    "text-[var(--text)] outline-none focus:border-[var(--accent)]/60";
  const labelClass = "mb-1.5 block text-xs font-medium text-[var(--text-muted)]";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={library ? `编辑「${library.name}」` : "添加媒体库"}
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-white/10 bg-[rgba(16,18,26,0.92)] shadow-[0_32px_90px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
        <div className="max-h-[76vh] space-y-4 overflow-y-auto p-6">
          <h2 className="text-[17px] font-bold text-white">
            {library ? "编辑媒体库" : "添加媒体库"}
            {library && (
              <span className="ml-2 text-[13px] font-normal text-[var(--text-muted)]">
                {library.name}
              </span>
            )}
          </h2>

          {error && (
            <p className="rounded-lg border border-red-400/25 bg-red-500/10 px-3.5 py-2.5 text-[13px] leading-6 text-red-200">
              {error}
            </p>
          )}

          {/* 类型：创建后不可改（订阅按类型挂库） */}
          <div>
            <label className={labelClass}>库类型{library ? "（创建后不可修改）" : ""}</label>
            <div className="flex flex-wrap gap-2">
              {(Object.keys(LIBRARY_KIND_META) as MediaType[]).map((k) => (
                <button
                  key={k}
                  type="button"
                  disabled={library !== null}
                  onClick={() => setKind(k)}
                  data-active={(library?.kind ?? kind) === k}
                  className="glass-row nav-item !w-auto px-3 py-1.5 text-xs font-medium disabled:opacity-60"
                >
                  {LIBRARY_KIND_META[k].label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className={labelClass}>名称</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="如：电影库 / 动漫库"
              autoComplete="off"
              className={inputClass}
            />
          </div>

          <div>
            <label className={labelClass}>根路径（第一个为主根）</label>
            <div className="space-y-1.5">
              {roots.map((root, i) => (
                <div
                  key={root}
                  className="group flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2"
                >
                  <FolderIcon className="size-4 shrink-0 text-[var(--accent)]/80" />
                  {/* 保尾截断（dir=rtl）：路径的区分信息在尾部，省略号出现在头部；
                      LRM 标记防止首尾的 "/" 在 RTL 下跳位。点击可原位更改目录 */}
                  <Tooltip
                    content={
                      <>
                        <p className="mb-1 break-all font-mono text-[11px] text-[var(--text-muted)]">{root}</p>
                        点击更改：从当前路径开始重新选择目录。
                      </>
                    }
                  >
                    <button
                      type="button"
                      dir="rtl"
                      onClick={() => setPickerTarget(i)}
                      className="min-w-0 flex-1 truncate rounded text-left font-mono text-[13px] text-[var(--text)] transition-colors hover:text-[var(--accent)]"
                    >
                      {"‎" + root + "‎"}
                    </button>
                  </Tooltip>
                  {i === 0 ? (
                    <Tooltip
                      content={
                        <>
                          <strong>主根 = 新内容的落盘位置。</strong>
                          订阅与手动下载完成后，按「主根/标题 (年份)」建目录入库；
                          一个库可挂多个根，但写入点只有主根这一个。
                        </>
                      }
                    >
                      <span className="shrink-0 cursor-default rounded-full bg-[var(--accent)]/15 px-2 py-0.5 text-[10px] font-semibold text-[var(--accent)]">
                        主根
                      </span>
                    </Tooltip>
                  ) : (
                    <Tooltip content="把该路径设为新内容的落盘位置（移到列表第一位）。已有文件不会被移动。">
                      <button
                        type="button"
                        onClick={() => setRoots([root, ...roots.filter((r) => r !== root)])}
                        className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium text-[var(--text-faint)] opacity-0 transition-opacity hover:bg-white/10 hover:text-white group-hover:opacity-100"
                      >
                        设为主根
                      </button>
                    </Tooltip>
                  )}
                  <button
                    type="button"
                    aria-label={`移除 ${root}`}
                    onClick={() => setRoots(roots.filter((r) => r !== root))}
                    className="shrink-0 rounded-md p-1 text-[var(--text-faint)] transition-colors hover:bg-white/10 hover:text-white"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() => setPickerTarget("add")}
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-white/15 px-3 py-2.5 text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--accent)]/50 hover:text-white"
              >
                <PlusIcon className="size-4" />
                {roots.length === 0 ? "浏览服务器目录并添加" : "添加目录"}
              </button>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
              新入库的内容落在<strong className="font-medium text-[var(--text-muted)]">主根</strong>下：主根/标题
              (年份)。其余为扩展根：扫描与监控照常覆盖、存量入账，但不写入新内容，适合跨盘存放的旧内容。
            </p>
          </div>

          {/* —— 下载监听目录：独立于根路径，完成的下载自动搬进主根 —— */}
          <div>
            <label className={labelClass}>下载监听目录（可选）</label>
            <div className="space-y-1.5">
              {ingestDirs.map((dir, i) => (
                <div
                  key={dir.path}
                  className="group flex items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2"
                >
                  <DownloadIcon className="size-4 shrink-0 text-[var(--accent)]/80" />
                  <Tooltip
                    content={
                      <>
                        <p className="mb-1 break-all font-mono text-[11px] text-[var(--text-muted)]">
                          {dir.path}
                        </p>
                        点击更改：从当前路径开始重新选择目录。
                      </>
                    }
                  >
                    <button
                      type="button"
                      dir="rtl"
                      onClick={() => setIngestPicker(i)}
                      className="min-w-0 flex-1 truncate rounded text-left font-mono text-[13px] text-[var(--text)] transition-colors hover:text-[var(--accent)]"
                    >
                      {"‎" + dir.path + "‎"}
                    </button>
                  </Tooltip>
                  <Tooltip
                    content={
                      <>
                        <strong>点击切换搬运策略。</strong>
                        硬链接：秒完成、零占用、源文件继续做种，但要求监听目录与主根在同一文件系统；
                        复制：跨盘可用，耗时且占双份空间。
                      </>
                    }
                  >
                    <button
                      type="button"
                      onClick={() =>
                        setIngestDirs(
                          ingestDirs.map((d, idx) =>
                            idx === i
                              ? { ...d, strategy: d.strategy === "hardlink" ? "copy" : "hardlink" }
                              : d,
                          ),
                        )
                      }
                      className="shrink-0 rounded-full bg-[var(--accent)]/15 px-2 py-0.5 text-[10px] font-semibold text-[var(--accent)] transition-colors hover:bg-[var(--accent)]/30"
                    >
                      {dir.strategy === "hardlink" ? "硬链接" : "复制"}
                    </button>
                  </Tooltip>
                  <button
                    type="button"
                    aria-label={`移除 ${dir.path}`}
                    onClick={() => setIngestDirs(ingestDirs.filter((_, idx) => idx !== i))}
                    className="shrink-0 rounded-md p-1 text-[var(--text-faint)] transition-colors hover:bg-white/10 hover:text-white"
                  >
                    <XIcon className="size-3.5" />
                  </button>
                </div>
              ))}
              <button
                type="button"
                onClick={() => setIngestPicker("add")}
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-white/15 px-3 py-2.5 text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--accent)]/50 hover:text-white"
              >
                <PlusIcon className="size-4" />
                添加下载监听目录
              </button>
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
              监听目录里的内容<strong className="font-medium text-[var(--text-muted)]">下载完成后</strong>
              （无下载中标记且持续静默、探测通过）自动识别并按规范命名搬进主根，源文件原地保留
              （硬链接不占额外空间、可继续做种）。目录不能与库根路径重叠。
            </p>
          </div>

          <div className="flex items-center justify-end gap-3 pt-1">
            <button type="button" onClick={onClose} className="btn-glass h-9 px-4 text-[13px] font-medium">
              取消
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={!canSubmit}
              className="btn-accent h-9 rounded-full px-5 text-[13px] font-semibold disabled:opacity-40"
            >
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </div>
      </div>

      {/* 服务端目录选择器：追加时从最近添加的根起步，更改时从被改的根起步；
          追加去重，更改为原位替换（改主根仍是主根），撞上已有路径时合并去重 */}
      <DirectoryPicker
        open={pickerTarget !== null}
        initialPath={
          pickerTarget === "add" || pickerTarget === null
            ? roots.length > 0
              ? roots[roots.length - 1]
              : undefined
            : roots[pickerTarget]
        }
        onClose={() => setPickerTarget(null)}
        onSelect={(path) => {
          setRoots((prev) => {
            if (pickerTarget === "add" || pickerTarget === null) {
              return prev.includes(path) ? prev : [...prev, path];
            }
            const next = prev.map((r, idx) => (idx === pickerTarget ? path : r));
            return next.filter((r, idx) => r !== path || idx === pickerTarget);
          });
          setPickerTarget(null);
        }}
      />

      {/* 下载监听目录的选择器：新目录默认硬链接策略，行内可切换 */}
      <DirectoryPicker
        open={ingestPicker !== null}
        initialPath={
          ingestPicker === "add" || ingestPicker === null
            ? ingestDirs.length > 0
              ? ingestDirs[ingestDirs.length - 1].path
              : undefined
            : ingestDirs[ingestPicker].path
        }
        onClose={() => setIngestPicker(null)}
        onSelect={(path) => {
          setIngestDirs((prev) => {
            if (ingestPicker === "add" || ingestPicker === null) {
              return prev.some((d) => d.path === path)
                ? prev
                : [...prev, { path, strategy: "hardlink" }];
            }
            const next = prev.map((d, idx) => (idx === ingestPicker ? { ...d, path } : d));
            return next.filter((d, idx) => d.path !== path || idx === ingestPicker);
          });
          setIngestPicker(null);
        }}
      />
    </div>
  );
}
