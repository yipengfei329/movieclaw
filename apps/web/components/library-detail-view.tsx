"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import type { Route } from "next";
import Link from "next/link";

import { XIcon } from "@/components/icons";
import { Breadcrumb } from "@/components/breadcrumb";
import { usePageTitle } from "@/lib/use-page-title";
import {
  LIBRARY_KIND_META,
  LibraryFormDialog,
  effectiveLibraryId,
  libraryCardAction,
} from "@/components/library-view";
import { LibraryOrganizeDialog } from "@/components/library-organize-dialog";
import { PosterCardVisual, type PosterVisualItem } from "@/components/poster-card";
import {
  type LibraryItem,
  type MediaLibrary,
  type MissingItem,
  type UnidentifiedFile,
  claimFile,
  clearMissing,
  clearUnidentified,
  ignoreFile,
  listLibraries,
  listLibraryItems,
  listMissing,
  listUnidentified,
  redownloadMissing,
  startLibraryScan,
  stopLibraryScan,
} from "@/lib/api/libraries";
import { listSubscriptions, type Subscription } from "@/lib/api/subscriptions";
import { formatBytes } from "@/lib/format";
import { formatRelativeTime } from "@/lib/time";
import { cachedImageUrl } from "@/lib/image-proxy";
import {
  subscriptionProgressNote,
  subscriptionStatusMeta,
} from "@/lib/subscription-ui";

/**
 * 单库页（/library/[id]）：库头部 + **真实库存**海报墙（Emby 进库后的浏览视图）。
 *
 * 三个分区：
 * 1. 库存（library_file 台账聚合）：已在磁盘上的作品，格下标注集数/规格/大小；
 * 2. 待识别：扫描认不出身份的文件，行内认领（填 TMDB ID）或忽略；
 * 3. 追踪中：入库目标是本库、但还没有文件落地的订阅（弱化展示，点进订阅详情）。
 */
export function LibraryDetailView({ libraryId }: { libraryId: number }) {
  const [libraries, setLibraries] = useState<MediaLibrary[] | null>(null);
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [unidentified, setUnidentified] = useState<UnidentifiedFile[]>([]);
  const [missing, setMissing] = useState<MissingItem[]>([]);
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<MediaLibrary | null>(null);
  // 整理文件名对话框的目标库；null = 关闭
  const [organizeTarget, setOrganizeTarget] = useState<MediaLibrary | null>(null);
  // 工单抽屉：从哪个胶囊点进来就落在哪个 tab；null = 关闭
  const [issueTab, setIssueTab] = useState<"missing" | "unidentified" | null>(null);

  const reload = useCallback(() => {
    setFailed(false);
    Promise.all([
      listLibraries(),
      listLibraryItems(libraryId).catch(() => []),
      listUnidentified(libraryId).catch(() => []),
      listMissing(libraryId).catch(() => []),
      listSubscriptions().catch(() => []),
    ])
      .then(([libs, libraryItems, unknown, missingItems, subs]) => {
        setLibraries(libs);
        setItems(libraryItems);
        setUnidentified(unknown);
        setMissing(missingItems);
        setSubscriptions(subs);
      })
      .catch(() => setFailed(true));
  }, [libraryId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const library = libraries?.find((l) => l.id === libraryId) ?? null;
  usePageTitle(library?.name);

  // 扫描/整理期间轮询，结束自动展示最新库存与文件名
  const busy = Boolean(library?.scanning || library?.organizing);
  // 写入中暂缓入账的文件数（watchdog 已发现、等拷贝/下载落定后自动补扫入库）
  const importing = busy ? 0 : (library?.last_scan?.deferred ?? 0);
  useEffect(() => {
    // 忙时快轮询；有文件入库中中速跟进补扫结果；空闲低频兜底——后台自发
    // 的扫描（实时监控/定时对账）页面开着不动也能感知到
    const interval = busy ? 3000 : importing > 0 ? 10_000 : 30_000;
    const timer = setInterval(reload, interval);
    return () => clearInterval(timer);
  }, [busy, importing, reload]);

  // 追踪中：目标是本库、且尚未在库存中出现的订阅
  const pending = useMemo(() => {
    if (!libraries || !library) return [];
    const ownedMedia = new Set(items.map((i) => i.media_item_id));
    return subscriptions.filter(
      (s) =>
        effectiveLibraryId(s, libraries) === library.id &&
        !ownedMedia.has(s.media.media_item_id ?? -1) &&
        s.progress.imported === 0,
    );
  }, [libraries, library, subscriptions, items]);

  if (failed) {
    return (
      <CenteredNote>
        <p className="text-[13.5px] text-[var(--text-muted)]">媒体库加载失败</p>
        <button
          type="button"
          onClick={reload}
          className="btn-glass px-4 py-2 text-[13px] font-medium text-[var(--text)]"
        >
          重试
        </button>
      </CenteredNote>
    );
  }

  if (libraries === null) {
    return (
      <CenteredNote>
        <span className="size-4 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
        <p className="text-[13px] text-[var(--text-muted)]">正在加载媒体库…</p>
      </CenteredNote>
    );
  }

  if (library === null) {
    return (
      <CenteredNote>
        <p className="text-[13.5px] text-[var(--text-muted)]">这个媒体库不存在（可能已被删除）</p>
        <Link href={"/library" as Route} className="btn-glass px-4 py-2 text-[13px] font-medium">
          返回媒体库
        </Link>
      </CenteredNote>
    );
  }

  const meta = LIBRARY_KIND_META[library.kind];
  const { stats } = library;

  return (
    <div className="scroll-thin flex-1 overflow-y-auto pb-10">
      {/* —— 库头部 —— */}
      <div className="px-6 pt-2">
        {/* 面包屑：媒体库 › 库名 */}
        <Breadcrumb items={[{ label: "媒体库", href: "/library" }, { label: library.name }]} />
        <div className="mt-2 flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <span className="icon-chip flex size-9 shrink-0 items-center justify-center !rounded-xl">
                <meta.Icon className="size-5" />
              </span>
              <h2 className="text-on-image truncate text-[26px] font-bold leading-tight tracking-[-0.02em] text-white">
                {library.name}
              </h2>
              {library.is_default && (
                <span className="shrink-0 rounded-full border border-white/[0.14] bg-white/[0.12] px-2 py-0.5 text-[11px] font-semibold text-white/90">
                  默认
                </span>
              )}
            </div>
            <p
              className="text-on-image mt-1.5 truncate text-[13px] text-[var(--text-muted)]"
              title={library.root_paths.join("\n")}
            >
              {meta.label}库 · {stats.item_count} 部作品 · {stats.file_count} 个文件 ·{" "}
              {formatBytes(stats.total_size_bytes)}
              {library.primary_root ? ` · ${library.primary_root}` : ""}
            </p>
            {library.last_scan && !busy && (
              <p className="mt-1 text-[12px] text-white/45">
                最近扫描 {formatRelativeTime(library.last_scan.finished_at)}
                {library.last_scan.cancelled ? "（手动停止，未扫完）" : ""} · 新入账{" "}
                {library.last_scan.scanned}（识别 {library.last_scan.identified} / 待识别{" "}
                {library.last_scan.unidentified}）
                {library.last_scan.marked_missing > 0
                  ? ` · 标记丢失 ${library.last_scan.marked_missing}`
                  : ""}
                {library.last_scan.deferred > 0
                  ? ` · ${library.last_scan.deferred} 个写入中暂缓（稍后自动补扫）`
                  : ""}
                {library.last_scan.errors.length > 0
                  ? ` · ${library.last_scan.errors[0]}`
                  : ""}
              </p>
            )}
            {/* —— 健康状态胶囊：工单收进抽屉，海报墙保持干净 —— */}
            {(missing.length > 0 || unidentified.length > 0 || importing > 0 || busy) && (
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                {busy && (
                  <span className="flex items-center gap-1.5 rounded-full border border-[#7dd3fc]/35 bg-[#7dd3fc]/[0.12] px-3 py-1 text-[12px] font-semibold text-[#7dd3fc]">
                    <span className="size-3 animate-spin rounded-full border-[1.5px] border-[#7dd3fc]/30 border-t-[#7dd3fc]" />
                    {library.scanning
                      ? `正在扫描${
                          library.scan_progress && library.scan_progress.total > 0
                            ? ` ${library.scan_progress.processed}/${library.scan_progress.total}`
                            : ""
                        } · 识别到的内容会自动入库`
                      : "正在整理文件名 · 完成后自动刷新"}
                  </span>
                )}
                {importing > 0 && (
                  <span className="flex items-center gap-1.5 rounded-full border border-[#7dd3fc]/35 bg-[#7dd3fc]/[0.12] px-3 py-1 text-[12px] font-semibold text-[#7dd3fc]">
                    <span className="size-1.5 animate-pulse rounded-full bg-[#7dd3fc]" />
                    已发现 {importing} 个新文件 · 写入完成后自动入库
                  </span>
                )}
                {missing.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setIssueTab("missing")}
                    className="flex items-center gap-1.5 rounded-full border border-white/[0.14] bg-white/[0.06] px-3 py-1 text-[12px] font-semibold text-white/75 transition hover:bg-white/[0.12] hover:text-white"
                  >
                    <span className="size-1.5 rounded-full bg-white/40" />
                    {missing.length} 个条目缺失
                  </button>
                )}
                {unidentified.length > 0 && (
                  <button
                    type="button"
                    onClick={() => setIssueTab("unidentified")}
                    className="flex items-center gap-1.5 rounded-full border border-[#f5c451]/35 bg-[#f5c451]/[0.12] px-3 py-1 text-[12px] font-semibold text-[#f5c451] transition hover:bg-[#f5c451]/[0.22]"
                  >
                    <span className="size-1.5 rounded-full bg-[#f5c451]" />
                    {unidentified.length} 个文件待识别
                  </button>
                )}
              </div>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2.5">
            {/* 扫描中按钮切换为「停止扫描」（增量幂等：已入账的保留，剩余下次继续） */}
            <button
              type="button"
              disabled={busy && !library.scanning}
              onClick={() => {
                setNotice(null);
                void (library.scanning ? stopLibraryScan(library.id) : startLibraryScan(library.id))
                  .then(() => reload())
                  .catch((e) => setNotice((e as Error).message));
              }}
              className="btn-glass px-4 py-2 text-[13px] font-medium disabled:opacity-50"
            >
              {library.scanning ? (
                <span className="flex items-center gap-2">
                  <span className="size-3.5 animate-spin rounded-full border-2 border-white/25 border-t-white/80" />
                  停止扫描
                  {library.scan_progress && library.scan_progress.total > 0
                    ? ` ${Math.min(
                        100,
                        Math.round(
                          (library.scan_progress.processed / library.scan_progress.total) * 100,
                        ),
                      )}%`
                    : ""}
                </span>
              ) : (
                "扫描库"
              )}
            </button>
            <button
              type="button"
              disabled={busy && !library.organizing}
              onClick={() => {
                setNotice(null);
                setOrganizeTarget(library);
              }}
              className="btn-glass px-4 py-2 text-[13px] font-medium disabled:opacity-50"
            >
              {library.organizing ? (
                <span className="flex items-center gap-2">
                  <span className="size-3.5 animate-spin rounded-full border-2 border-white/25 border-t-white/80" />
                  整理中…
                  {library.organize_progress && library.organize_progress.total > 0
                    ? ` ${Math.min(
                        100,
                        Math.round(
                          (library.organize_progress.processed /
                            library.organize_progress.total) *
                            100,
                        ),
                      )}%`
                    : ""}
                </span>
              ) : (
                "整理文件名"
              )}
            </button>
            {/* 扫描/整理中锁定编辑：进行中的任务在按当前根路径读写台账 */}
            <button
              type="button"
              disabled={busy}
              title={busy ? "扫描/整理进行中，暂不能编辑" : undefined}
              onClick={() => setEditing(library)}
              className="btn-glass px-4 py-2 text-[13px] font-medium disabled:opacity-50"
            >
              编辑库
            </button>
          </div>
        </div>
        {notice && (
          <p className="mt-3 rounded-lg border border-red-400/25 bg-red-500/10 px-3.5 py-2 text-[12.5px] text-red-200">
            {notice}
          </p>
        )}
      </div>

      {/* —— 工单抽屉：缺失 / 待识别，从头部胶囊进入 —— */}
      <IssueDrawer
        open={issueTab}
        onClose={() => setIssueTab(null)}
        onSwitchTab={setIssueTab}
        libraryId={libraryId}
        missing={missing}
        unidentified={unidentified}
        movie={library.kind === "movie"}
        onChanged={reload}
      />

      {/* —— 库存海报墙 —— */}
      {items.length === 0 && unidentified.length === 0 ? (
        <p className="mt-16 text-center text-[13.5px] leading-7 text-[var(--text-muted)]">
          这个库还没有内容。
          <br />
          点「扫描库」把根路径下已有的影片识别入库；订阅的内容下载完成后也会自动进来。
        </p>
      ) : (
        <div className="mt-6 grid gap-x-4 gap-y-7 px-6 [grid-template-columns:repeat(auto-fill,minmax(148px,1fr))]">
          {items.map((item) => (
            <InventoryCell key={item.media_item_id} item={item} />
          ))}
        </div>
      )}

      {/* —— 追踪中（订阅已指向本库、文件未落地）—— */}
      {pending.length > 0 && (
        <div className="mt-10 px-6">
          <h3 className="text-on-image text-[15px] font-semibold text-white/85">
            追踪中
            <span className="ml-2 text-[12px] font-normal text-[var(--text-faint)]">
              已订阅、资源到位后自动入库
            </span>
          </h3>
          <div className="mt-4 grid gap-x-4 gap-y-7 [grid-template-columns:repeat(auto-fill,minmax(148px,1fr))]">
            {pending.map((sub) => (
              <PendingCell key={sub.id} sub={sub} />
            ))}
          </div>
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

/** 库存格：真实拥有的作品。点击进影片详情；格下标注库存概况。 */
function InventoryCell({ item }: { item: LibraryItem }) {
  const visual: PosterVisualItem = {
    id: String(item.tmdb_id),
    source: "tmdb",
    type: item.kind,
    title: item.title,
    year: item.year ?? undefined,
    rating: 0,
    posterUrl: item.poster_url ? cachedImageUrl(item.poster_url) : "",
  };
  const parts: string[] = [];
  if (item.kind === "tv") {
    if (item.seasons.length > 0) {
      parts.push(
        item.seasons.length === 1
          ? `第 ${item.seasons[0]} 季 · ${item.episode_count} 集`
          : `${item.seasons.length} 季 · ${item.episode_count} 集`,
      );
    }
  } else {
    parts.push(formatBytes(item.total_size_bytes));
  }
  if (item.resolutions.length > 0) parts.push(item.resolutions.join("/"));
  // 文件全部缺失的"死条目"：海报置灰，一眼与在位内容区分
  const dead = item.file_count > 0 && item.missing_count >= item.file_count;
  return (
    <div>
      <div className={dead ? "opacity-50 grayscale" : undefined}>
        <PosterCardVisual
          item={visual}
          href={`/media/${item.kind}/${item.tmdb_id}` as Route}
          action={libraryCardAction(item)}
        />
      </div>
      <p className="text-on-image mt-1.5 flex items-center gap-1.5 truncate text-[11px] text-[var(--text-muted)]">
        <span
          className={`size-1.5 shrink-0 rounded-full ${
            dead ? "bg-white/30" : item.missing_count > 0 ? "bg-[#f5c451]" : "bg-[#4ade80]"
          }`}
        />
        <span className="truncate">
          {dead
            ? "文件已全部缺失"
            : `${parts.join(" · ") || "已入库"}${
                item.missing_count > 0 ? ` · ${item.missing_count} 个文件缺失` : ""
              }`}
        </span>
      </p>
    </div>
  );
}

/** 追踪中格：订阅状态行沿用订阅页语言，点击进订阅详情。 */
function PendingCell({ sub }: { sub: Subscription }) {
  const meta = subscriptionStatusMeta[sub.status];
  const visual: PosterVisualItem = {
    id: String(sub.media.tmdb_id),
    source: "tmdb",
    title: sub.media.title,
    year: sub.media.year ?? undefined,
    rating: 0,
    posterUrl: sub.media.poster_url ? cachedImageUrl(sub.media.poster_url) : "",
  };
  return (
    <div className="opacity-80 transition hover:opacity-100">
      {/* 已是订阅产物，悬浮层不再给「订阅影片」重复入口 */}
      <PosterCardVisual item={visual} href={`/subscriptions/${sub.id}` as Route} action="none" />
      <p className="text-on-image mt-1.5 flex items-center gap-1.5 truncate text-[11px] text-[var(--text-muted)]">
        <span
          className="size-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: meta.color }}
        />
        <span className="truncate">
          {meta.label} · {subscriptionProgressNote(sub)}
        </span>
      </p>
    </div>
  );
}

/* —— 工单抽屉：缺失 / 待识别双 tab，右侧滑出，海报墙不再被工单铺满 —— */

function IssueDrawer({
  open,
  onClose,
  onSwitchTab,
  libraryId,
  missing,
  unidentified,
  movie,
  onChanged,
}: {
  open: "missing" | "unidentified" | null;
  onClose: () => void;
  onSwitchTab: (tab: "missing" | "unidentified") => void;
  libraryId: number;
  missing: MissingItem[];
  unidentified: UnidentifiedFile[];
  movie: boolean;
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  // 打开/切 tab 时清空过滤词；Escape 关闭
  useEffect(() => {
    setQuery("");
  }, [open]);
  useEffect(() => {
    if (open === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (open === null || typeof document === "undefined") return null;

  const keyword = query.trim().toLowerCase();
  const missingShown = keyword
    ? missing.filter((m) => m.title.toLowerCase().includes(keyword))
    : missing;
  const unidentifiedShown = keyword
    ? unidentified.filter((f) => f.file_path.toLowerCase().includes(keyword))
    : unidentified;
  const missingFileTotal = missing.reduce((n, item) => n + item.files.length, 0);

  const tabClass = (active: boolean) =>
    `rounded-full px-3.5 py-1.5 text-[12.5px] font-semibold transition ${
      active ? "bg-white/[0.14] text-white" : "text-[var(--text-muted)] hover:text-white"
    }`;

  return createPortal(
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="库工单">
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/50 backdrop-blur-[2px]"
      />
      <div className="absolute right-0 top-0 flex h-full w-full max-w-[600px] flex-col border-l border-white/10 bg-[rgba(16,18,26,0.94)] shadow-[-24px_0_70px_rgba(0,0,0,0.55)] backdrop-blur-2xl">
        {/* 头部：tab + 关闭 */}
        <div className="flex items-center justify-between gap-3 border-b border-white/[0.08] px-5 py-3.5">
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => onSwitchTab("missing")}
              className={tabClass(open === "missing")}
            >
              缺失 {missing.length > 0 ? missing.length : ""}
            </button>
            <button
              type="button"
              onClick={() => onSwitchTab("unidentified")}
              className={tabClass(open === "unidentified")}
            >
              待识别 {unidentified.length > 0 ? unidentified.length : ""}
            </button>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="关闭抽屉"
            className="btn-glass flex size-8 items-center justify-center !rounded-full"
          >
            <XIcon className="size-4" />
          </button>
        </div>

        {/* 工具行：过滤 + 批量操作 */}
        <div className="flex items-center gap-2.5 border-b border-white/[0.08] px-5 py-3">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={open === "missing" ? "按片名过滤…" : "按文件名过滤…"}
            className="min-w-0 flex-1 rounded-lg border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-[12.5px] text-[var(--text)] outline-none placeholder:text-white/30 focus:border-[var(--accent)]/60"
          />
          {open === "missing" && missing.length > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    `清理全部 ${missingFileTotal} 条缺失记录？（只删台账，不动磁盘）`,
                  )
                )
                  return;
                setBusy(true);
                void clearMissing(libraryId)
                  .then(onChanged)
                  .catch(() => {})
                  .finally(() => setBusy(false));
              }}
              className="btn-glass shrink-0 px-3 py-1.5 text-[12px] font-medium disabled:opacity-50"
            >
              全部清理
            </button>
          )}
          {open === "unidentified" && unidentified.length > 0 && (
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (
                  !window.confirm(
                    `忽略全部 ${unidentified.length} 个待识别文件？（只删台账，不动磁盘；文件还在时重新扫描会再次发现）`,
                  )
                )
                  return;
                setBusy(true);
                void clearUnidentified(libraryId)
                  .then(onChanged)
                  .catch(() => {})
                  .finally(() => setBusy(false));
              }}
              className="btn-glass shrink-0 px-3 py-1.5 text-[12px] font-medium disabled:opacity-50"
            >
              全部忽略
            </button>
          )}
        </div>

        {/* 说明行 */}
        <p className="px-5 pt-3 text-[11.5px] leading-5 text-[var(--text-muted)]">
          {open === "missing"
            ? "文件已不在磁盘；「重新下载」交给订阅管线补回，「清理记录」只删台账（都不动磁盘）；文件回归会自动恢复。"
            : "扫描时无法确认身份；填入 TMDB ID 认领，或从台账忽略（都不动磁盘文件）。"}
        </p>

        {/* 列表区：独立滚动 */}
        <div className="scroll-thin min-h-0 flex-1 space-y-2 overflow-y-auto px-5 py-4">
          {open === "missing" &&
            missingShown.map((item) => (
              <MissingRow
                key={item.media_item_id}
                libraryId={libraryId}
                item={item}
                onChanged={onChanged}
              />
            ))}
          {open === "unidentified" &&
            unidentifiedShown.map((file) => (
              <UnidentifiedRow key={file.id} file={file} movie={movie} onChanged={onChanged} />
            ))}
          {((open === "missing" && missingShown.length === 0) ||
            (open === "unidentified" && unidentifiedShown.length === 0)) && (
            <p className="mt-12 text-center text-[13px] text-[var(--text-muted)]">
              {keyword ? "没有匹配的条目" : "没有需要处理的了 🎉"}
            </p>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function MissingRow({
  libraryId,
  item,
  onChanged,
}: {
  libraryId: number;
  item: MissingItem;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const act = (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    void fn()
      .then(onChanged)
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  // 剧集：按季聚合缺失集数做摘要；电影：单文件
  const summary =
    item.kind === "tv"
      ? Array.from(
          item.files.reduce((m, f) => {
            m.set(f.season_number, (m.get(f.season_number) ?? 0) + 1);
            return m;
          }, new Map<number, number>()),
        )
          .sort(([a], [b]) => a - b)
          .map(([s, n]) => (s === 0 ? `特别篇 ${n} 集` : `第 ${s} 季缺 ${n} 集`))
          .join("、")
      : `${item.files.length} 个文件`;

  return (
    <div className="rounded-xl bg-white/[0.03] px-3.5 py-2.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-white/85">
          {item.title}
          {item.year ? ` (${item.year})` : ""}
          <span className="ml-2 text-[12px] font-normal text-[var(--text-muted)]">{summary}</span>
        </span>
        {done ? (
          <span className="text-[12px] text-[#4ade80]">{done}</span>
        ) : (
          <span className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                act(() =>
                  redownloadMissing(libraryId, item.media_item_id).then(({ requeued }) => {
                    setDone(`已交给订阅管线（${requeued} 个工单排队）`);
                  }),
                )
              }
              className="btn-accent rounded-full px-3 py-1.5 text-[12px] font-semibold disabled:opacity-50"
            >
              重新下载
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                const warning = item.subscription_id
                  ? `「${item.title}」有正在追踪的订阅，只清记录的话订阅可能把它重新下回来。仍要清理这 ${item.files.length} 条缺失记录？`
                  : `清理「${item.title}」的 ${item.files.length} 条缺失记录？（只删台账，不动磁盘）`;
                if (!window.confirm(warning)) return;
                act(() => clearMissing(libraryId, item.media_item_id));
              }}
              className="btn-glass px-3 py-1.5 text-[12px] font-medium disabled:opacity-50"
            >
              清理记录
            </button>
          </span>
        )}
      </div>
      {item.subscription_id && !done && (
        <p className="mt-1 text-[11.5px] text-[#f5c451]/80">该条目有订阅在追踪</p>
      )}
      {error && <p className="mt-1 text-[11.5px] text-red-300">{error}</p>}
    </div>
  );
}

/* —— 待识别行：行内认领（TMDB ID + 季集）或忽略 —— */

function UnidentifiedRow({
  file,
  movie,
  onChanged,
}: {
  file: UnidentifiedFile;
  movie: boolean;
  onChanged: () => void;
}) {
  const [tmdbId, setTmdbId] = useState("");
  const [season, setSeason] = useState(String(file.season_number || ""));
  const [episode, setEpisode] = useState(String(file.episode_number || ""));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inputClass =
    "w-24 rounded-lg border border-white/[0.08] bg-white/[0.04] px-2.5 py-1.5 text-[12px] " +
    "text-[var(--text)] outline-none focus:border-[var(--accent)]/60";

  const act = (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    void fn()
      .then(onChanged)
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="rounded-xl bg-white/[0.03] px-3.5 py-2.5">
      <p className="truncate font-mono text-[11.5px] text-[var(--text-muted)]" title={file.file_path}>
        {file.file_path}
      </p>
      {/* 识别失败原因：TMDB 访问失败（重扫可解）与真找不到（需人工认领）区分开 */}
      {file.reason && (
        <p className="mt-1 text-[11.5px] leading-5 text-[#f5c451]/90">{file.reason}</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          type="text"
          inputMode="numeric"
          value={tmdbId}
          onChange={(e) => setTmdbId(e.target.value)}
          placeholder="TMDB ID"
          className={inputClass}
        />
        {!movie && (
          <>
            <input
              type="text"
              inputMode="numeric"
              value={season}
              onChange={(e) => setSeason(e.target.value)}
              placeholder="季"
              className={`${inputClass} !w-14`}
            />
            <input
              type="text"
              inputMode="numeric"
              value={episode}
              onChange={(e) => setEpisode(e.target.value)}
              placeholder="集"
              className={`${inputClass} !w-14`}
            />
          </>
        )}
        <button
          type="button"
          disabled={busy || !/^\d+$/.test(tmdbId)}
          onClick={() =>
            act(() =>
              claimFile(file.id, {
                tmdb_id: Number(tmdbId),
                season_number: movie ? 0 : Number(season) || 0,
                episode_number: movie ? 0 : Number(episode) || 0,
              }),
            )
          }
          className="btn-accent rounded-full px-3.5 py-1.5 text-[12px] font-semibold disabled:opacity-40"
        >
          认领
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => act(() => ignoreFile(file.id))}
          className="btn-glass px-3 py-1.5 text-[12px] font-medium disabled:opacity-40"
        >
          忽略
        </button>
        <span className="text-[11px] text-[var(--text-faint)]">
          {formatBytes(file.size_bytes)}
        </span>
        {error && <span className="text-[11.5px] text-red-300">{error}</span>}
      </div>
    </div>
  );
}

function CenteredNote({ children }: { children: React.ReactNode }) {
  return (
    <div className="mt-16 flex flex-col items-center gap-3 text-center">{children}</div>
  );
}
