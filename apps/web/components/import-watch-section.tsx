"use client";

import { useCallback, useEffect, useState } from "react";

import { DirectoryPicker } from "@/components/directory-picker";
import { FolderIcon, PlusIcon, XIcon } from "@/components/icons";
import { type ConfiguredDownloader, listDownloaders } from "@/lib/api/downloaders";
import {
  type ImportWatchRule,
  createImportWatchRule,
  deleteImportWatchRule,
  listImportWatchRules,
  updateImportWatchRule,
} from "@/lib/api/import-watch";
import { type MediaLibrary, listLibraries } from "@/lib/api/libraries";

/** 下载器目录候选：源目录大概率就是下载器的某个目录，供表单一键填入。 */
interface DownloaderDirOption {
  /** movieclaw 视角的目录（默认保存目录或路径映射左列） */
  path: string;
  /** 来源下载器名称（chip 的 title 提示用） */
  downloaderName: string;
}

/** 从下载器配置里收集 movieclaw 视角的目录候选（去重，保持配置顺序）。 */
function collectDownloaderDirs(downloaders: ConfiguredDownloader[]): DownloaderDirOption[] {
  const seen = new Set<string>();
  const options: DownloaderDirOption[] = [];
  for (const d of downloaders) {
    const paths = [d.save_path, ...(d.path_mappings ?? []).map((m) => m.local)];
    for (const path of paths) {
      if (!path || seen.has(path)) continue;
      seen.add(path);
      options.push({ path, downloaderName: d.name });
    }
  }
  return options;
}

/**
 * 监听导入配置（设置 → 监听导入）：媒体库之上的独立功能。
 *
 * 每条规则 = 监听一个源目录，目录里下载完成的内容（下载器确认或指纹
 * 静默 + 探测通过）自动识别并按规范命名搬进目标库主根。媒体库本身
 * 只有一套目录体系、不承载下载语义——需要"下载区 → 库"搬运的用户
 * 在这里独立配置，不需要的用户永远不会看到这个概念。
 */
export function ImportWatchSection() {
  const [rules, setRules] = useState<ImportWatchRule[] | null>(null);
  const [libraries, setLibraries] = useState<MediaLibrary[]>([]);
  const [downloaderDirs, setDownloaderDirs] = useState<DownloaderDirOption[]>([]);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 编辑态："new" = 新建 / 规则对象 = 编辑 / null = 关闭
  const [editing, setEditing] = useState<ImportWatchRule | "new" | null>(null);

  const reload = useCallback(() => {
    setFailed(false);
    Promise.all([listImportWatchRules(), listLibraries()])
      .then(([ruleRows, libs]) => {
        setRules(ruleRows);
        setLibraries(libs);
      })
      .catch(() => setFailed(true));
    // 下载器目录只是表单的快捷候选，拉取失败不影响主功能，静默降级为无候选
    void listDownloaders()
      .then((rows) => setDownloaderDirs(collectDownloaderDirs(rows)))
      .catch(() => setDownloaderDirs([]));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const remove = (rule: ImportWatchRule) => {
    if (
      !window.confirm(
        `删除对 ${rule.source_path} 的监听？源目录与已导入的文件都不受影响，只是停止监听。`,
      )
    )
      return;
    setError(null);
    void deleteImportWatchRule(rule.id)
      .then(reload)
      .catch((e) => setError((e as Error).message));
  };

  return (
    <div className="space-y-5">
      <p className="text-[13px] leading-6 text-[var(--text-muted)]">
        监听下载目录，其中<strong className="font-medium text-white/80">下载完成</strong>
        的内容（下载器确认完成，或文件持续静默且探测通过）自动识别、按「标题 (年份)」规范命名
        搬进目标媒体库。源文件原地保留：硬链接零占用、可继续做种；复制适合跨盘。
        把下载器的保存目录设为这里的源目录，即可实现「下载完成自动整理入库」。
      </p>

      {error && (
        <p className="rounded-lg border border-red-400/25 bg-red-500/10 px-3.5 py-2.5 text-[13px] leading-6 text-red-200">
          {error}
        </p>
      )}

      {failed && (
        <div className="flex items-center gap-3">
          <p className="text-[13px] text-[var(--text-muted)]">监听导入配置加载失败</p>
          <button type="button" onClick={reload} className="btn-glass px-3 py-1.5 text-[12.5px] font-medium">
            重试
          </button>
        </div>
      )}

      {rules !== null && !failed && (
        <div className="space-y-2">
          {rules.length === 0 && (
            <p className="rounded-xl bg-white/[0.03] px-4 py-6 text-center text-[13px] text-[var(--text-muted)]">
              还没有监听导入规则。不需要「下载区 → 库」自动搬运的话，这里保持为空即可。
            </p>
          )}
          {rules.map((rule) => (
            <div
              key={rule.id}
              className="flex items-center gap-3 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3.5 py-2.5"
            >
              <FolderIcon className="size-4 shrink-0 text-[var(--accent)]/80" />
              <div className="min-w-0 flex-1">
                <p className="truncate font-mono text-[12.5px] text-[var(--text)]" title={rule.source_path}>
                  {rule.source_path}
                </p>
                <p className="mt-0.5 text-[11.5px] text-[var(--text-muted)]">
                  {rule.strategy === "hardlink" ? "硬链接" : "复制"} → {rule.library_name}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setEditing(rule)}
                className="btn-glass shrink-0 px-3 py-1.5 text-[12px] font-medium"
              >
                编辑
              </button>
              <button
                type="button"
                aria-label={`删除对 ${rule.source_path} 的监听`}
                onClick={() => remove(rule)}
                className="shrink-0 rounded-md p-1.5 text-[var(--text-faint)] transition-colors hover:bg-white/10 hover:text-white"
              >
                <XIcon className="size-4" />
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={() => setEditing("new")}
            className="flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-white/15 px-3 py-2.5 text-[13px] font-medium text-[var(--text-muted)] transition-colors hover:border-[var(--accent)]/50 hover:text-white"
          >
            <PlusIcon className="size-4" />
            添加监听导入规则
          </button>
        </div>
      )}

      <RuleFormDialog
        state={editing}
        libraries={libraries}
        downloaderDirs={downloaderDirs}
        onClose={() => setEditing(null)}
        onSaved={() => {
          setEditing(null);
          reload();
        }}
      />
    </div>
  );
}

/* —— 新建 / 编辑规则的弹窗（库表单同款视觉） —— */

function RuleFormDialog({
  state,
  libraries,
  downloaderDirs,
  onClose,
  onSaved,
}: {
  state: ImportWatchRule | "new" | null;
  libraries: MediaLibrary[];
  /** 下载器的本地目录候选：源目录大概率就是其中之一（或其子目录） */
  downloaderDirs: DownloaderDirOption[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const rule = state === "new" ? null : state;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourcePath, setSourcePath] = useState("");
  const [strategy, setStrategy] = useState<"hardlink" | "copy">("hardlink");
  const [libraryId, setLibraryId] = useState<number | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);

  useEffect(() => {
    if (state === null) return;
    setError(null);
    setSourcePath(rule?.source_path ?? "");
    setStrategy(rule?.strategy ?? "hardlink");
    setLibraryId(rule?.library_id ?? libraries[0]?.id ?? null);
    setPickerOpen(false);
  }, [state, rule, libraries]);

  useEffect(() => {
    if (state === null) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, onClose]);

  if (state === null) return null;

  const canSubmit = !busy && sourcePath.length > 0 && libraryId !== null;

  const submit = () => {
    if (libraryId === null) return;
    setBusy(true);
    setError(null);
    const payload = { source_path: sourcePath, strategy, library_id: libraryId };
    void (rule ? updateImportWatchRule(rule.id, payload) : createImportWatchRule(payload))
      .then(onSaved)
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  const labelClass = "mb-1.5 block text-xs font-medium text-[var(--text-muted)]";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-6"
      role="dialog"
      aria-modal="true"
      aria-label={rule ? "编辑监听导入规则" : "添加监听导入规则"}
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />
      <div className="relative w-full max-w-md overflow-hidden rounded-2xl border border-white/10 bg-[rgba(16,18,26,0.92)] shadow-[0_32px_90px_rgba(0,0,0,0.7)] backdrop-blur-2xl">
        <div className="space-y-4 p-6">
          <h2 className="text-[17px] font-bold text-white">
            {rule ? "编辑监听导入规则" : "添加监听导入规则"}
          </h2>

          {error && (
            <p className="rounded-lg border border-red-400/25 bg-red-500/10 px-3.5 py-2.5 text-[13px] leading-6 text-red-200">
              {error}
            </p>
          )}

          <div>
            <label className={labelClass}>源目录（监听这里的下载）</label>
            <button
              type="button"
              onClick={() => setPickerOpen(true)}
              className="flex w-full items-center gap-2 rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-left transition-colors hover:border-[var(--accent)]/50"
            >
              <FolderIcon className="size-4 shrink-0 text-[var(--accent)]/80" />
              {sourcePath ? (
                <span dir="rtl" className="min-w-0 flex-1 truncate font-mono text-[13px] text-[var(--text)]">
                  {"‎" + sourcePath + "‎"}
                </span>
              ) : (
                <span className="text-[13px] text-[var(--text-faint)]">浏览服务器目录并选择…</span>
              )}
            </button>
            {/* 下载器目录快捷候选：源目录大概率就是下载器目录，点选直填；
                想用其子目录（如 watch/）点选后再「浏览」，弹窗会从该目录起步 */}
            {downloaderDirs.length > 0 && (
              <div className="mt-2">
                <p className="mb-1.5 text-[11px] text-[var(--text-faint)]">
                  从下载器目录快速选择（选后可再浏览细化到子目录）：
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {downloaderDirs.map((option) => (
                    <button
                      key={option.path}
                      type="button"
                      onClick={() => setSourcePath(option.path)}
                      data-active={sourcePath === option.path}
                      title={`${option.path}（来自下载器「${option.downloaderName}」）`}
                      className="glass-row nav-item !w-auto max-w-full gap-1.5 px-2.5 py-1.5 text-[12px] font-medium"
                    >
                      <FolderIcon className="size-3.5 shrink-0 text-[var(--accent)]/80" />
                      <span dir="rtl" className="min-w-0 truncate font-mono">
                        {"‎" + option.path + "‎"}
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
            <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
              不能与任何媒体库的根路径重叠（库根下的内容由库自己扫描管理）。
              订阅和手动下载会把种子投到这个目录（movieclaw 视角）；若下载器在另一个
              容器/主机上、看到的路径不同，请先到「设置 → 下载器」配置路径映射，
              否则会下载到错误位置。
            </p>
          </div>

          <div>
            <label className={labelClass}>搬运策略</label>
            <div className="flex gap-2">
              {(
                [
                  ["hardlink", "硬链接", "零占用、源文件继续做种；需与目标库主根同一文件系统"],
                  ["copy", "复制", "跨盘可用；耗时且占双份空间"],
                ] as const
              ).map(([value, label, hint]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setStrategy(value)}
                  data-active={strategy === value}
                  className="glass-row nav-item !w-auto px-3 py-1.5 text-xs font-medium"
                  title={hint}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
              {strategy === "hardlink"
                ? "保存时会检测源目录与目标库主根是否同一文件系统，跨盘会提示改用复制。"
                : "复制适合源目录与库不在同一块盘的部署。"}
            </p>
          </div>

          <div>
            <label className={labelClass}>目标媒体库（导入落其主根）</label>
            <div className="flex flex-wrap gap-2">
              {libraries.map((lib) => (
                <button
                  key={lib.id}
                  type="button"
                  onClick={() => setLibraryId(lib.id)}
                  data-active={libraryId === lib.id}
                  className="glass-row nav-item !w-auto px-3 py-1.5 text-xs font-medium"
                  title={lib.primary_root ?? undefined}
                >
                  {lib.name}
                </button>
              ))}
              {libraries.length === 0 && (
                <p className="text-[12px] text-[var(--text-faint)]">
                  还没有媒体库，请先在「媒体库」页创建。
                </p>
              )}
            </div>
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

      <DirectoryPicker
        open={pickerOpen}
        initialPath={sourcePath || undefined}
        onClose={() => setPickerOpen(false)}
        onSelect={(path) => {
          setSourcePath(path);
          setPickerOpen(false);
        }}
      />
    </div>
  );
}
