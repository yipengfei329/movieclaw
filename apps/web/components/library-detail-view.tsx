"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import type { Route } from "next";
import Link from "next/link";

import { ChevronLeftIcon } from "@/components/icons";
import {
  LIBRARY_KIND_META,
  LibraryFormDialog,
  effectiveLibraryId,
} from "@/components/library-view";
import { PosterCardVisual, type PosterVisualItem } from "@/components/poster-card";
import {
  type LibraryItem,
  type MediaLibrary,
  type UnidentifiedFile,
  claimFile,
  ignoreFile,
  listLibraries,
  listLibraryItems,
  listUnidentified,
  startLibraryScan,
} from "@/lib/api/libraries";
import { listSubscriptions, type Subscription } from "@/lib/api/subscriptions";
import { formatBytes } from "@/lib/format";
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
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [failed, setFailed] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<MediaLibrary | null>(null);

  const reload = useCallback(() => {
    setFailed(false);
    Promise.all([
      listLibraries(),
      listLibraryItems(libraryId).catch(() => []),
      listUnidentified(libraryId).catch(() => []),
      listSubscriptions().catch(() => []),
    ])
      .then(([libs, libraryItems, unknown, subs]) => {
        setLibraries(libs);
        setItems(libraryItems);
        setUnidentified(unknown);
        setSubscriptions(subs);
      })
      .catch(() => setFailed(true));
  }, [libraryId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const library = libraries?.find((l) => l.id === libraryId) ?? null;

  // 扫描期间轮询，结束自动展示新库存
  useEffect(() => {
    if (!library?.scanning) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [library?.scanning, reload]);

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
        <Link
          href={"/library" as Route}
          className="text-on-image inline-flex items-center gap-1 text-[12.5px] font-medium text-[var(--text-muted)] transition hover:text-white"
        >
          <ChevronLeftIcon className="size-4" />
          媒体库
        </Link>
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
          </div>
          <div className="flex shrink-0 items-center gap-2.5">
            <button
              type="button"
              disabled={library.scanning}
              onClick={() => {
                setNotice(null);
                void startLibraryScan(library.id)
                  .then(() => reload())
                  .catch((e) => setNotice((e as Error).message));
              }}
              className="btn-glass px-4 py-2 text-[13px] font-medium disabled:opacity-50"
            >
              {library.scanning ? (
                <span className="flex items-center gap-2">
                  <span className="size-3.5 animate-spin rounded-full border-2 border-white/25 border-t-white/80" />
                  扫描中…
                </span>
              ) : (
                "扫描库"
              )}
            </button>
            <button
              type="button"
              onClick={() => setEditing(library)}
              className="btn-glass px-4 py-2 text-[13px] font-medium"
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

      {/* —— 待识别（有才显示）—— */}
      {unidentified.length > 0 && (
        <UnidentifiedPanel
          files={unidentified}
          movie={library.kind === "movie"}
          onChanged={reload}
        />
      )}

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
  return (
    <div>
      <PosterCardVisual
        item={visual}
        href={`/media/${item.kind}/${item.tmdb_id}` as Route}
      />
      <p className="text-on-image mt-1.5 flex items-center gap-1.5 truncate text-[11px] text-[var(--text-muted)]">
        <span className="size-1.5 shrink-0 rounded-full bg-[#4ade80]" />
        <span className="truncate">
          {parts.join(" · ") || "已入库"}
          {item.missing_count > 0 ? ` · ${item.missing_count} 个文件缺失` : ""}
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
      <PosterCardVisual item={visual} href={`/subscriptions/${sub.id}` as Route} />
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

/* —— 待识别面板：行内认领（TMDB ID + 季集）或忽略 —— */

function UnidentifiedPanel({
  files,
  movie,
  onChanged,
}: {
  files: UnidentifiedFile[];
  movie: boolean;
  onChanged: () => void;
}) {
  return (
    <div className="mx-6 mt-6 rounded-2xl border border-[#f5c451]/25 bg-[#f5c451]/[0.06] p-4">
      <h3 className="text-[13.5px] font-semibold text-[#f5c451]">
        {files.length} 个文件待识别
        <span className="ml-2 text-[12px] font-normal text-[var(--text-muted)]">
          扫描时无法确认身份；填入 TMDB ID 认领，或从台账忽略（都不动磁盘文件）
        </span>
      </h3>
      <div className="mt-3 space-y-2">
        {files.map((file) => (
          <UnidentifiedRow key={file.id} file={file} movie={movie} onChanged={onChanged} />
        ))}
      </div>
    </div>
  );
}

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
