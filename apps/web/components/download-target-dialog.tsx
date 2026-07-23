"use client";

/**
 * 下载目标选择弹窗（手动下载的保存位置确认）。
 *
 * 点搜索结果的「下载」先弹出本层，让用户明确文件会落到哪，而不是静默
 * 提交后再猜。候选来源分三层：
 *   1. 智能入库（种子解析出可靠身份时）：走后端与订阅同源的三级兜底
 *      （监听目录 / 库条目目录），并用 dispatch-preview 预检结论展示
 *      真实归宿与配置警示；
 *   2. 下载器已配置的目录：默认保存目录 + 各路径映射的 movieclaw 侧目录，
 *      双视角展示（movieclaw 路径 → 下载器路径），跨容器部署一眼可核对；
 *   3. 下载器默认目录兜底：不指定路径，由下载器自行决定。
 * 底部小字引导去「设置 → 下载器」配置路径映射。
 */

import { useEffect, useMemo, useState } from "react";

import { FolderIcon } from "@/components/icons";
import {
  listDownloaders,
  submitTorrentDownload,
  type ConfiguredDownloader,
  type DownloadSubmitResult,
  type PathMapping,
} from "@/lib/api/downloaders";
import { defaultLibraryFor, type MediaLibrary } from "@/lib/api/libraries";
import { getDispatchPreview, type DispatchPreview } from "@/lib/api/subscriptions";

/** 弹窗需要的种子身份切片（由搜索结果的 TorrentHit 提炼）。 */
export interface DownloadTargetRequest {
  site_id: string;
  download_url: string;
  /** 解析出的条目身份；三件套不全时为 null（智能入库选项不出现） */
  identity: { kind: "movie" | "tv"; title: string; year: number } | null;
  subtitle: string | null;
}

/** 与后端 translate_save_path 同规则的前端版（仅用于展示下载器视角）。 */
function toRemoteView(path: string, mappings: PathMapping[] | null): string {
  if (!mappings) return path;
  let best: PathMapping | null = null;
  for (const m of mappings) {
    const local = m.local.replace(/\/+$/, "");
    if (
      (path === local || path.startsWith(local + "/")) &&
      (best === null || local.length > best.local.replace(/\/+$/, "").length)
    ) {
      best = m;
    }
  }
  if (!best) return path;
  const local = best.local.replace(/\/+$/, "");
  return best.remote.replace(/\/+$/, "") + path.slice(local.length);
}

/** 一个可选的保存目标。 */
interface TargetOption {
  key: string;
  kind: "smart" | "dir" | "default";
  /** 提交时的 save_path（smart/default 为 null，走各自的后端语义） */
  savePath: string | null;
  label: string;
  /** 次级说明（路径、双视角、警示等） */
  detail: string | null;
  warning: string | null;
}

export function DownloadTargetDialog({
  request,
  onClose,
  onSubmitted,
}: {
  /** null = 关闭 */
  request: DownloadTargetRequest | null;
  onClose: () => void;
  onSubmitted: (result: DownloadSubmitResult) => void;
}) {
  const [downloader, setDownloader] = useState<ConfiguredDownloader | null>(null);
  const [library, setLibrary] = useState<MediaLibrary | null>(null);
  const [preview, setPreview] = useState<DispatchPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 打开时并行拉取：默认下载器（目录候选）、默认库 + 投递预检（智能入库项）
  useEffect(() => {
    if (!request) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    setSelected(null);
    setPreview(null);
    setLibrary(null);
    const identity = request.identity;
    void Promise.all([
      listDownloaders().catch(() => [] as ConfiguredDownloader[]),
      identity ? defaultLibraryFor(identity.kind).catch(() => null) : Promise.resolve(null),
    ]).then(async ([downloaders, lib]) => {
      if (cancelled) return;
      setDownloader(downloaders.find((d) => d.is_default) ?? null);
      setLibrary(lib);
      if (identity && lib) {
        const p = await getDispatchPreview(identity.kind, lib.id).catch(() => null);
        if (!cancelled) setPreview(p);
      }
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [request]);

  const options = useMemo<TargetOption[]>(() => {
    if (!request) return [];
    const result: TargetOption[] = [];
    if (request.identity && library) {
      const folder = `${request.identity.title} (${request.identity.year})`;
      const detail =
        preview?.mode === "watch"
          ? `投递到监听导入目录 ${preview.path}，完成后自动整理入库`
          : preview
            ? `直接下载到 ${preview.path?.replace(/\/+$/, "")}/${folder}，完成后自动入账`
            : null;
      result.push({
        key: "smart",
        kind: "smart",
        savePath: null,
        label: `自动入库到「${library.name}」`,
        detail,
        warning: preview && !preview.ok ? preview.warning : null,
      });
    }
    const seen = new Set<string>();
    const dirs: { path: string; source: string }[] = [];
    if (downloader?.save_path) dirs.push({ path: downloader.save_path, source: "默认保存目录" });
    for (const m of downloader?.path_mappings ?? []) {
      if (!seen.has(m.local) && m.local !== downloader?.save_path) {
        dirs.push({ path: m.local, source: "路径映射" });
      }
      seen.add(m.local);
    }
    for (const dir of dirs) {
      const remote = toRemoteView(dir.path, downloader?.path_mappings ?? null);
      result.push({
        key: `dir:${dir.path}`,
        kind: "dir",
        savePath: dir.path,
        label: dir.path,
        detail: remote !== dir.path ? `下载器视角：${remote}（${dir.source}）` : dir.source,
        warning: null,
      });
    }
    result.push({
      key: "default",
      kind: "default",
      savePath: null,
      label: "下载器默认目录",
      detail: "不指定路径，由下载器按自身设置决定；movieclaw 不会自动整理入库",
      warning: null,
    });
    return result;
  }, [request, library, preview, downloader]);

  // 默认选中：智能入库可用且预检通过 > 第一个目录 > 下载器默认
  useEffect(() => {
    if (loading || options.length === 0 || selected !== null) return;
    const smart = options.find((o) => o.kind === "smart");
    if (smart && !smart.warning) setSelected(smart.key);
    else setSelected(options[0].key);
  }, [loading, options, selected]);

  if (!request) return null;

  const submit = () => {
    const option = options.find((o) => o.key === selected);
    if (!option || busy) return;
    setBusy(true);
    setError(null);
    const identity = request.identity;
    void submitTorrentDownload({
      site_id: request.site_id,
      download_url: request.download_url,
      ...(option.kind === "smart" && identity && library
        ? {
            library_id: library.id,
            title: identity.title,
            year: identity.year,
            subtitle: request.subtitle,
          }
        : {}),
      ...(option.kind === "dir" ? { save_path: option.savePath } : {}),
    })
      .then((result) => {
        onSubmitted(result);
        onClose();
      })
      .catch((e) => setError(e instanceof Error ? e.message : "提交失败，请重试"))
      .finally(() => setBusy(false));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label="选择保存位置"
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-white/10 bg-[rgba(16,18,26,0.92)] shadow-[0_32px_90px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
        <div className="space-y-4 p-6">
          <h2 className="text-[17px] font-bold text-white">选择保存位置</h2>

          {error && (
            <p className="rounded-lg border border-red-400/25 bg-red-500/10 px-3.5 py-2.5 text-[13px] leading-6 text-red-200">
              {error}
            </p>
          )}

          {loading ? (
            <div className="space-y-2">
              <div className="h-[52px] animate-pulse rounded-xl bg-white/[0.04]" />
              <div className="h-[52px] animate-pulse rounded-xl bg-white/[0.04]" />
            </div>
          ) : (
            <div className="space-y-2">
              {options.map((option) => (
                <button
                  key={option.key}
                  type="button"
                  onClick={() => setSelected(option.key)}
                  data-active={selected === option.key}
                  className="flex w-full items-start gap-2.5 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3.5 py-2.5 text-left transition-colors hover:border-[var(--accent)]/50 data-[active=true]:border-[var(--accent)]/70 data-[active=true]:bg-[var(--accent-soft)]"
                >
                  <FolderIcon className="mt-0.5 size-4 shrink-0 text-[var(--accent)]/80" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-[13px] font-medium text-[var(--text)]">
                      {option.label}
                    </span>
                    {option.detail && (
                      <span className="mt-0.5 block text-[11px] leading-relaxed text-[var(--text-faint)]">
                        {option.detail}
                      </span>
                    )}
                    {option.warning && (
                      <span className="mt-1 block rounded-md bg-amber-500/10 px-2 py-1 text-[11px] leading-relaxed text-amber-200">
                        {option.warning}
                      </span>
                    )}
                  </span>
                </button>
              ))}
            </div>
          )}

          <p className="text-[11px] leading-relaxed text-[var(--text-faint)]">
            movieclaw 与下载器不在同一容器/主机、看到的路径不同？到
            <a href="/settings/downloaders" className="mx-0.5 text-[var(--accent)] hover:underline">
              设置 → 下载器
            </a>
            配置路径映射，提交时会自动翻译成下载器视角。
          </p>

          <div className="flex justify-end gap-3 pt-1">
            <button type="button" onClick={onClose} className="btn-glass h-9 px-4 text-[13px] font-medium">
              取消
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={busy || loading || selected === null}
              className="btn-accent h-9 rounded-full px-5 text-[13px] font-semibold disabled:opacity-40"
            >
              {busy ? "提交中…" : "确认下载"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
