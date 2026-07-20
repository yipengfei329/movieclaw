"use client";

import { useCallback, useEffect, useState } from "react";

import type { Route } from "next";
import Link from "next/link";
import { createPortal } from "react-dom";

import { FilmIcon, MoreIcon, PlusIcon, TvIcon } from "@/components/icons";
import {
  type LibraryItem,
  type LibraryPayload,
  type MediaLibrary,
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
import type { MediaType } from "@/lib/media-types";

/** 库类型 → 展示名与图标 */
export const LIBRARY_KIND_META: Record<MediaType, { label: string; Icon: typeof FilmIcon }> = {
  movie: { label: "电影", Icon: FilmIcon },
  tv: { label: "剧集", Icon: TvIcon },
};

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
 * 每张卡是一个库：封面用库内作品的海报拼图（最多 4 张，参考 moviebot 的
 * 库封面合成，但在前端用 CSS 拼、零后端开销），叠库名/类型/统计；
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

  // 有库在扫描时轮询刷新，扫描完成即看到新库存
  const scanningAny = (libraries ?? []).some((l) => l.scanning);
  useEffect(() => {
    if (!scanningAny) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [scanningAny, reload]);

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
        <div className="mt-6 grid gap-5 px-6 [grid-template-columns:repeat(auto-fill,minmax(320px,1fr))]">
          {libraries.map((library) => (
            <LibraryCard
              key={library.id}
              library={library}
              items={itemsByLibrary.get(library.id) ?? []}
              onEdit={() => setEditing(library)}
              onRefresh={reload}
              onError={setError}
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
    </div>
  );
}

/* —— 库卡片：海报拼图封面 + 库名/徽标/计数，Emby「我的媒体」磁贴风 —— */

function LibraryCard({
  library,
  items,
  onEdit,
  onRefresh,
  onError,
}: {
  library: MediaLibrary;
  items: LibraryItem[];
  onEdit: () => void;
  onRefresh: () => void;
  onError: (message: string) => void;
}) {
  const meta = LIBRARY_KIND_META[library.kind];
  const posters = items
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
        <div className="relative aspect-[16/9] bg-[#141824]">
          <LibraryCover posters={posters} Icon={meta.Icon} />
          {/* 底部渐变压暗，托住文字（Emby 磁贴同款处理） */}
          <div className="absolute inset-0 bg-gradient-to-t from-black/85 via-black/25 to-black/10 transition group-hover/lib:via-black/35" />

          <div className="absolute inset-x-0 bottom-0 p-4">
            <div className="flex items-center gap-2">
              <span className="icon-chip flex size-7 shrink-0 items-center justify-center !rounded-lg">
                <meta.Icon className="size-4" />
              </span>
              <h3 className="truncate text-[17px] font-bold text-white">{library.name}</h3>
              {library.is_default && (
                <span className="shrink-0 rounded-full border border-white/[0.14] bg-white/[0.14] px-2 py-0.5 text-[10.5px] font-semibold text-white/90 backdrop-blur-sm">
                  默认
                </span>
              )}
              {library.scanning && (
                <span className="flex shrink-0 items-center gap-1.5 rounded-full border border-white/[0.14] bg-black/40 px-2 py-0.5 text-[10.5px] font-semibold text-white/90 backdrop-blur-sm">
                  <span className="size-2.5 animate-spin rounded-full border border-white/30 border-t-white/90" />
                  扫描中
                </span>
              )}
              {stats.unidentified_count > 0 && (
                <span className="shrink-0 rounded-full border border-[#f5c451]/40 bg-[#f5c451]/15 px-2 py-0.5 text-[10.5px] font-semibold text-[#f5c451] backdrop-blur-sm">
                  {stats.unidentified_count} 个待识别
                </span>
              )}
            </div>
            <p className="mt-1 truncate pl-9 text-[11.5px] text-white/60" title={library.primary_root ?? undefined}>
              {meta.label} · {summary}
              {library.primary_root ? ` · ${library.primary_root}` : ""}
            </p>
          </div>
        </div>
      </Link>

      {/* 管理操作：悬停浮现在右上角（Link 外层，避免点菜单触发跳转） */}
      <LibraryCardMenu
        library={library}
        onEdit={onEdit}
        onScan={() => {
          void startLibraryScan(library.id)
            .then(onRefresh)
            .catch((e) => onError((e as Error).message));
        }}
        onRefresh={onRefresh}
        onError={onError}
      />
    </div>
  );
}

/** 封面拼图：4 张=2×2 网格；1-3 张=首图铺满；0 张=类型图标底纹。 */
function LibraryCover({ posters, Icon }: { posters: string[]; Icon: typeof FilmIcon }) {
  if (posters.length === 0) {
    return (
      <div className="absolute inset-0 flex items-center justify-center bg-gradient-to-br from-[#1c2230] to-[#10131c]">
        <Icon className="size-12 text-white/[0.13]" />
      </div>
    );
  }
  if (posters.length < 4) {
    return (
      <img
        src={cachedImageUrl(posters[0])}
        alt=""
        loading="lazy"
        referrerPolicy="no-referrer"
        className="absolute inset-0 size-full scale-105 object-cover object-[center_20%] blur-[1px]"
      />
    );
  }
  return (
    <div className="absolute inset-0 grid grid-cols-4">
      {posters.map((url, i) => (
        <img
          key={i}
          src={cachedImageUrl(url)}
          alt=""
          loading="lazy"
          referrerPolicy="no-referrer"
          className="size-full object-cover"
        />
      ))}
    </div>
  );
}

/** 卡片右上角的管理菜单（Portal 到 body，同侧栏会话菜单的处理）。 */
function LibraryCardMenu({
  library,
  onEdit,
  onScan,
  onRefresh,
  onError,
}: {
  library: MediaLibrary;
  onEdit: () => void;
  onScan: () => void;
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
              disabled={library.scanning}
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
  // 根路径用多行文本编辑：每行一个绝对路径，第一行为主根
  const [rootsText, setRootsText] = useState("");

  // 每次打开时按目标重置表单（编辑带入现值，新增清空）
  useEffect(() => {
    if (state === null) return;
    setError(null);
    setKind(library?.kind ?? "movie");
    setName(library?.name ?? "");
    setRootsText(library?.root_paths.join("\n") ?? "");
  }, [state, library]);

  useEffect(() => {
    if (state === null) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, onClose]);

  if (state === null) return null;

  const roots = rootsText
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  const canSubmit = !busy && name.trim().length > 0 && roots.length > 0;

  const submit = () => {
    setBusy(true);
    setError(null);
    const payload: LibraryPayload = { name: name.trim(), kind, root_paths: roots };
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
            <label className={labelClass}>根路径（每行一个，第一行为主根）</label>
            <textarea
              value={rootsText}
              onChange={(e) => setRootsText(e.target.value)}
              rows={3}
              placeholder={"/vol1/media/movies\n/vol2/media/movies（扩展根，可选）"}
              className={`${inputClass} resize-y font-mono`}
            />
            <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
              新入库的内容落在<strong className="font-medium text-[var(--text-muted)]">主根</strong>下：主根/标题
              (年份)。填绝对路径；扩展根用于跨盘存量内容的盘点（后续版本接入扫描）。
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
    </div>
  );
}
