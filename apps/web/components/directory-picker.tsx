"use client";

/**
 * 服务端目录选择器（DirectoryPicker）。
 *
 * 媒体库的根路径在服务器上，用户手打绝对路径极易出错。这个弹层像
 * Jellyfin 的目录浏览器一样工作：面包屑逐级导航 + 子目录列表点击下钻，
 * 底部确认「选择此目录」。同时保留手动输入兜底（点铅笔图标切换）——
 * 远程部署时用户可能记得路径但不想逐级点。
 *
 * 数据源是后端 /fs/browse（只读、仅目录）。组件自身是独立的全屏层
 * （z-60，压在库表单弹窗 z-50 之上），键盘 Escape 只关闭自己。
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { CheckIcon, ChevronRightIcon, FolderIcon, PencilIcon } from "@/components/icons";
import { browseFs, type FsBrowse } from "@/lib/api/fs";

export function DirectoryPicker({
  open,
  initialPath,
  onClose,
  onSelect,
}: {
  open: boolean;
  /** 打开时的起始目录；无效或缺省时回落到根目录 */
  initialPath?: string | null;
  onClose: () => void;
  /** 用户确认选择：回传当前目录的绝对路径 */
  onSelect: (path: string) => void;
}) {
  const [view, setView] = useState<FsBrowse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const listRef = useRef<HTMLDivElement | null>(null);
  const crumbRef = useRef<HTMLDivElement | null>(null);

  // 深路径下面包屑会溢出，每次导航后滚到最右端，让当前层级始终可见
  useEffect(() => {
    const el = crumbRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [view]);

  const navigate = useCallback(async (path?: string) => {
    setLoading(true);
    setError(null);
    try {
      const next = await browseFs(path);
      setView(next);
      setEditing(false);
      listRef.current?.scrollTo({ top: 0 });
    } catch (e) {
      // 跳转失败保留当前列表，只提示错误（如手动输入了不存在的路径）
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // 打开时从起始目录进入；起始目录无效（已被删除等）则回落根目录
  useEffect(() => {
    if (!open) return;
    setView(null);
    setError(null);
    setEditing(false);
    void (async () => {
      setLoading(true);
      try {
        setView(await browseFs(initialPath ?? undefined));
      } catch {
        try {
          setView(await browseFs());
        } catch (e) {
          setError((e as Error).message);
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [open, initialPath]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // capture 阶段拦截，避免同一个 Escape 把下层的库表单弹窗也关掉；
      // 手动输入态下 Esc 只退出输入，不关弹层
      e.stopPropagation();
      if (editing) setEditing(false);
      else onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, editing, onClose]);

  if (!open) return null;

  // 面包屑：根 "/" + 逐级路径段，点任意一段跳回该层
  const segments =
    view?.path
      .split("/")
      .filter(Boolean)
      .map((name, i, arr) => ({ name, path: "/" + arr.slice(0, i + 1).join("/") })) ?? [];

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label="选择服务器目录"
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />
      <div className="relative flex max-h-[76vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl border border-white/10 bg-[rgba(16,18,26,0.95)] shadow-[0_32px_90px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
        <div className="space-y-3 p-5 pb-3">
          <h2 className="text-[15px] font-bold text-white">选择服务器目录</h2>

          {/* 面包屑 / 手动输入（铅笔切换） */}
          {editing ? (
            <input
              autoFocus
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void navigate(draft.trim());
              }}
              placeholder="输入绝对路径后回车跳转"
              spellCheck={false}
              className="w-full rounded-xl border border-[var(--accent)]/60 bg-white/[0.04] px-3 py-2 font-mono text-[13px] text-[var(--text)] outline-none"
            />
          ) : (
            <div className="flex items-center gap-1.5">
              <div
                ref={crumbRef}
                className="scroll-none flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto rounded-xl border border-white/[0.08] bg-white/[0.04] px-2 py-1.5"
              >
                <button
                  type="button"
                  onClick={() => void navigate("/")}
                  className="shrink-0 rounded-md px-1.5 py-0.5 font-mono text-[13px] text-[var(--text-muted)] hover:bg-white/10 hover:text-white"
                >
                  /
                </button>
                {segments.map((seg) => (
                  <span key={seg.path} className="flex shrink-0 items-center gap-0.5">
                    <ChevronRightIcon className="size-3 shrink-0 text-[var(--text-faint)]" />
                    <button
                      type="button"
                      onClick={() => void navigate(seg.path)}
                      className={`rounded-md px-1.5 py-0.5 text-[13px] hover:bg-white/10 hover:text-white ${
                        seg.path === view?.path
                          ? "font-semibold text-white"
                          : "text-[var(--text-muted)]"
                      }`}
                    >
                      {seg.name}
                    </button>
                  </span>
                ))}
              </div>
              <button
                type="button"
                title="手动输入路径"
                onClick={() => {
                  setDraft(view?.path ?? "/");
                  setEditing(true);
                }}
                className="btn-glass flex size-8 shrink-0 items-center justify-center !rounded-lg"
              >
                <PencilIcon className="size-3.5" />
              </button>
            </div>
          )}

          {error && (
            <p className="rounded-lg border border-red-400/25 bg-red-500/10 px-3 py-2 text-[12px] leading-5 text-red-200">
              {error}
            </p>
          )}
        </div>

        {/* 子目录列表：单击下钻 */}
        <div ref={listRef} className="min-h-[240px] flex-1 overflow-y-auto px-3 pb-2">
          {loading && !view ? (
            <p className="px-3 py-8 text-center text-[13px] text-[var(--text-muted)]">加载中…</p>
          ) : view && view.entries.length === 0 ? (
            <p className="px-3 py-8 text-center text-[13px] text-[var(--text-muted)]">
              该目录下没有子目录，可直接选择当前目录
            </p>
          ) : (
            view?.entries.map((entry) => (
              <button
                key={entry.path}
                type="button"
                disabled={loading}
                onClick={() => void navigate(entry.path)}
                className="glass-row group flex w-full items-center gap-2.5 px-3 py-2 text-left disabled:opacity-50"
              >
                <FolderIcon className="size-4 shrink-0 text-[var(--accent)]/80" />
                <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)]">
                  {entry.name}
                </span>
                <ChevronRightIcon className="size-3.5 shrink-0 text-[var(--text-faint)] opacity-0 transition-opacity group-hover:opacity-100" />
              </button>
            ))
          )}
        </div>

        {/* 底栏：当前选择 + 确认 */}
        <div className="flex items-center gap-3 border-t border-white/[0.08] bg-white/[0.02] px-5 py-3.5">
          {/* 保尾截断（dir=rtl）：省略号在头部，当前目录名始终可见；LRM 防 "/" 跳位 */}
          <p
            dir="rtl"
            className="min-w-0 flex-1 truncate text-left font-mono text-[12px] text-[var(--text-muted)]"
            title={view?.path}
          >
            {view ? "‎" + view.path + "‎" : "…"}
          </p>
          <button type="button" onClick={onClose} className="btn-glass h-8 shrink-0 px-3.5 text-[13px] font-medium">
            取消
          </button>
          <button
            type="button"
            disabled={!view || loading}
            onClick={() => view && onSelect(view.path)}
            className="btn-accent flex h-8 shrink-0 items-center gap-1.5 rounded-full px-4 text-[13px] font-semibold disabled:opacity-40"
          >
            <CheckIcon className="size-3.5" />
            选择此目录
          </button>
        </div>
      </div>
    </div>
  );
}
