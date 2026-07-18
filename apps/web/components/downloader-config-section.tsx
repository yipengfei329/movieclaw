"use client";

import { useCallback, useEffect, useState } from "react";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";

import { DownloadIcon, MoreIcon, PlusIcon } from "@/components/icons";
import { useBackdrop } from "@/lib/backdrop";
import {
  type ConfiguredDownloader,
  type DownloaderClientType,
  type DownloaderPayload,
  type DownloaderStatus,
  createDownloader,
  deleteDownloader,
  listDownloaders,
  reverifyDownloader,
  setDefaultDownloader,
  setDownloaderEnabled,
  updateDownloader,
} from "@/lib/api/downloaders";
import { formatRelativeTime } from "@/lib/time";
import { LiquidGlassButton } from "@/vendor/liquid-glass";

/** 连接状态 → 展示文案与颜色（与站点配置同语言） */
const STATUS_META: Record<DownloaderStatus, { label: string; color: string }> = {
  active: { label: "已连接", color: "#4ade80" },
  verifying: { label: "测试中", color: "#6aa7ff" },
  pending: { label: "待测试", color: "#c0c4cc" },
  failed: { label: "连接失败", color: "#ff6b6b" },
};

/** 下载器类型 → 展示名 */
const TYPE_LABEL: Record<DownloaderClientType, string> = {
  qbittorrent: "qBittorrent",
  transmission: "Transmission",
};

/** 各类型的地址占位提示（qB 是 WebUI 地址，Tr 是 RPC 地址，端口不同） */
const URL_PLACEHOLDER: Record<DownloaderClientType, string> = {
  qbittorrent: "http://192.168.1.10:8080",
  transmission: "http://192.168.1.10:9091",
};

/** 需要轮询测试进度的中间态 */
const IN_PROGRESS: DownloaderStatus[] = ["pending", "verifying"];

/**
 * 「下载器」设置分区。
 *
 * 与站点配置同构：列表展示已接入的下载器，「添加下载器」展开表单；
 * 保存后后端异步测试连接，前端对中间态（pending/verifying）轮询刷新，
 * 直到 active / failed。搜索结果里的"提交下载"以这里配置的实例为目标。
 */
export function DownloaderConfigSection() {
  const [downloaders, setDownloaders] = useState<ConfiguredDownloader[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // 是否展开「添加下载器」面板
  const [adding, setAdding] = useState(false);
  // 当前展开编辑表单的下载器 id（null 表示没有展开的编辑表单）
  const [editing, setEditing] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setDownloaders(await listDownloaders());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 有下载器处于 pending/verifying 时轮询刷新，直到全部落定
  const hasInProgress = downloaders.some((d) => IN_PROGRESS.includes(d.status));
  useEffect(() => {
    if (!hasInProgress) return;
    const timer = setInterval(() => {
      void listDownloaders()
        .then(setDownloaders)
        .catch(() => {
          /* 轮询失败静默重试，不打断页面 */
        });
    }, 2000);
    return () => clearInterval(timer);
  }, [hasInProgress]);

  // 原地替换已有条目、新条目追加到末尾（保持列表顺序稳定，避免操作后跳位）
  const upsert = useCallback((next: ConfiguredDownloader) => {
    setDownloaders((prev) => {
      const idx = prev.findIndex((d) => d.id === next.id);
      if (idx === -1) return [...prev, next];
      const copy = [...prev];
      copy[idx] = next;
      return copy;
    });
  }, []);

  const usableCount = downloaders.filter((d) => d.usable).length;

  return (
    <div className="space-y-5">
      {error && (
        <div className="rounded-xl border border-[#ff6b6b]/30 bg-[#ff6b6b]/10 px-4 py-3 text-sm text-[#ff6b6b]">
          {error}
        </div>
      )}

      <div className="flex items-center justify-between gap-4">
        <p className="text-xs text-[var(--text-muted)]">
          {loading
            ? "加载中…"
            : downloaders.length === 0
              ? "接入你自己部署的下载软件，资源将由它们完成下载。"
              : `已接入 ${downloaders.length} 个下载器，${usableCount} 个可用。保存后系统会自动测试连接。`}
        </p>
        <div className="flex shrink-0 items-center gap-2.5">
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="btn-glass px-3.5 py-1.5 text-xs font-medium"
          >
            刷新
          </button>
          <button
            type="button"
            onClick={() => setAdding((v) => !v)}
            disabled={loading}
            className="btn-accent flex items-center gap-1 rounded-full py-1.5 pl-2.5 pr-3.5 text-xs font-semibold disabled:opacity-60"
          >
            <PlusIcon className="size-4" />
            添加下载器
          </button>
        </div>
      </div>

      {/* 「添加下载器」面板 */}
      {adding && (
        <div className="css-glass !rounded-xl p-4">
          <DownloaderForm
            downloader={null}
            onSubmit={async (payload) => {
              upsert(await createDownloader(payload));
              setAdding(false);
            }}
            onCancel={() => setAdding(false)}
            onError={setError}
          />
        </div>
      )}

      {/* 已配置下载器列表 */}
      <div className="space-y-2.5">
        {loading ? (
          <>
            <div className="h-[72px] animate-pulse rounded-xl bg-white/[0.04]" />
            <div className="h-[72px] animate-pulse rounded-xl bg-white/[0.04]" />
          </>
        ) : downloaders.length === 0 ? (
          <div className="css-glass flex flex-col items-center gap-3 !rounded-2xl px-6 py-12 text-center">
            <span className="icon-chip size-12 !rounded-2xl">
              <DownloadIcon className="size-6" />
            </span>
            <div>
              <p className="text-sm font-medium text-[var(--text)]">还没有接入任何下载器</p>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                点击右上角「添加下载器」，支持 qBittorrent 和 Transmission。
              </p>
            </div>
          </div>
        ) : (
          downloaders.map((downloader) => (
            <DownloaderCard
              key={downloader.id}
              downloader={downloader}
              expanded={editing === downloader.id}
              onToggleForm={(open) => setEditing(open ? downloader.id : null)}
              onChanged={upsert}
              onDeleted={(id) => {
                setDownloaders((prev) => prev.filter((d) => d.id !== id));
                setEditing((cur) => (cur === id ? null : cur));
                // 删除默认时后端会把默认让给另一台，整体刷新拿到新归属
                void load();
              }}
              onRefresh={() => void load()}
              onError={setError}
            />
          ))
        )}
      </div>
    </div>
  );
}

/* —— 单个下载器卡片：折叠态展示状态 + 操作，展开态是连接表单 —— */

interface DownloaderCardProps {
  downloader: ConfiguredDownloader;
  expanded: boolean;
  onToggleForm: (open: boolean) => void;
  onChanged: (downloader: ConfiguredDownloader) => void;
  onDeleted: (id: number) => void;
  /** 需要整体刷新列表的操作（如设默认会同时改动其他条目）之后调用 */
  onRefresh: () => void;
  onError: (message: string) => void;
}

function DownloaderCard({
  downloader,
  expanded,
  onToggleForm,
  onChanged,
  onDeleted,
  onRefresh,
  onError,
}: DownloaderCardProps) {
  const { backdrop } = useBackdrop();
  const [busy, setBusy] = useState(false);
  const meta = STATUS_META[downloader.status];

  async function guard(fn: () => Promise<void>) {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      onError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // 副标题：失败时给出原因，正常时展示 类型 + 版本 + 上次检查时间
  const subtitle =
    downloader.status === "failed" && downloader.last_error
      ? downloader.last_error
      : [
          TYPE_LABEL[downloader.client_type],
          downloader.version,
          `上次检查 ${formatRelativeTime(downloader.last_checked_at)}`,
        ]
          .filter(Boolean)
          .join(" · ");

  return (
    <div className="css-glass !rounded-xl">
      <div className="flex items-center gap-3.5 p-4">
        <span className="icon-chip size-10 !rounded-xl">
          <DownloadIcon className="size-5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-semibold text-[var(--text)]">{downloader.name}</p>
            {downloader.is_default && (
              <span className="shrink-0 rounded-full border border-white/[0.12] bg-[var(--accent-soft)] px-2 py-0.5 text-[11px] font-semibold text-[var(--accent)]">
                默认
              </span>
            )}
            <span
              className="flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium"
              style={{ background: `${meta.color}1f`, color: meta.color }}
            >
              <span className="size-1.5 rounded-full" style={{ background: meta.color }} />
              {meta.label}
            </span>
          </div>
          <p className="mt-0.5 truncate text-[11px] text-[var(--text-faint)]" title={downloader.url}>
            {subtitle}
          </p>
        </div>

        <div className="flex shrink-0 items-center gap-2">
          {/* 启用开关：与站点卡片同款受控 WebGL 液态玻璃开关 */}
          <LiquidGlassButton
            backgroundImage={backdrop}
            variant="dark"
            checked={downloader.enabled}
            disabled={busy}
            aria-label={downloader.enabled ? "已启用，点击停用" : "已停用，点击启用"}
            onCheckedChange={(enabled) =>
              void guard(async () => onChanged(await setDownloaderEnabled(downloader.id, enabled)))
            }
            className="!min-h-0 !w-auto !gap-0 !bg-transparent !p-0"
          >
            <span className="sr-only">{downloader.enabled ? "已启用" : "已停用"}</span>
          </LiquidGlassButton>
          <DownloaderActionsMenu
            expanded={expanded}
            busy={busy}
            isDefault={downloader.is_default}
            canReverify={!IN_PROGRESS.includes(downloader.status)}
            onEdit={() => onToggleForm(!expanded)}
            onSetDefault={() =>
              void guard(async () => {
                await setDefaultDownloader(downloader.id);
                // 原默认的标记同时被清掉，整体刷新一次拿到全量新状态
                onRefresh();
              })
            }
            onReverify={() =>
              void guard(async () => onChanged(await reverifyDownloader(downloader.id)))
            }
            onDelete={() =>
              void guard(async () => {
                if (!window.confirm(`确定删除「${downloader.name}」？`)) return;
                await deleteDownloader(downloader.id);
                onDeleted(downloader.id);
              })
            }
          />
        </div>
      </div>

      {/* 连接信息带：地址与默认保存目录，一眼可核对 */}
      <div className="flex flex-wrap gap-x-7 gap-y-2 border-t border-white/[0.06] px-4 py-3">
        <InfoStat label="地址" value={downloader.url} />
        {downloader.username && <InfoStat label="用户名" value={downloader.username} />}
        <InfoStat label="默认保存目录" value={downloader.save_path ?? "下载器默认"} />
      </div>

      {/* 展开态：编辑表单 */}
      {expanded && (
        <div className="border-t border-white/[0.06] p-4">
          <DownloaderForm
            downloader={downloader}
            onSubmit={async (payload) => {
              onChanged(await updateDownloader(downloader.id, payload));
              onToggleForm(false);
            }}
            onCancel={() => onToggleForm(false)}
            onError={onError}
          />
        </div>
      )}
    </div>
  );
}

function InfoStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <p className="text-[10px] text-[var(--text-faint)]">{label}</p>
      <p className="mt-0.5 truncate text-[13px] font-semibold text-[var(--text)]">{value}</p>
    </div>
  );
}

/* —— 下载器操作折叠菜单（与站点卡片同款 Radix DropdownMenu） —— */

interface DownloaderActionsMenuProps {
  expanded: boolean;
  busy: boolean;
  /** 已是默认时「设为默认」置灰 */
  isDefault: boolean;
  canReverify: boolean;
  onEdit: () => void;
  onSetDefault: () => void;
  onReverify: () => void;
  onDelete: () => void;
}

function DownloaderActionsMenu({
  expanded,
  busy,
  isDefault,
  canReverify,
  onEdit,
  onSetDefault,
  onReverify,
  onDelete,
}: DownloaderActionsMenuProps) {
  const itemClass =
    "glass-row nav-item cursor-pointer px-3 py-2 text-xs font-medium outline-none " +
    "data-[highlighted]:!bg-[var(--glass-fill-hover)] data-[highlighted]:!text-[var(--text)] " +
    "data-[disabled]:pointer-events-none data-[disabled]:opacity-40";

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label="更多操作"
          className="glass-row !w-auto p-1.5 data-[state=open]:!bg-[var(--glass-fill-active)] data-[state=open]:!text-[var(--text)]"
        >
          <MoreIcon className="size-4" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          align="end"
          sideOffset={6}
          collisionPadding={12}
          className="css-glass z-50 min-w-[9rem] !rounded-xl p-1 shadow-xl backdrop-blur-xl"
        >
          <DropdownMenu.Item onSelect={onEdit} className={itemClass}>
            {expanded ? "收起编辑" : "编辑配置"}
          </DropdownMenu.Item>
          <DropdownMenu.Item
            onSelect={onSetDefault}
            disabled={busy || isDefault}
            className={itemClass}
          >
            设为默认
          </DropdownMenu.Item>
          <DropdownMenu.Item
            onSelect={onReverify}
            disabled={busy || !canReverify}
            className={itemClass}
          >
            重新测试连接
          </DropdownMenu.Item>
          <DropdownMenu.Item
            onSelect={onDelete}
            disabled={busy}
            className={`${itemClass} !text-[#ff6b6b] data-[highlighted]:!bg-[#ff6b6b]/10`}
          >
            删除配置
          </DropdownMenu.Item>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

/* —— 连接表单：类型 + 名称 + 地址 + 可选凭证/保存目录，新增与编辑共用 —— */

interface DownloaderFormProps {
  /** 编辑对象；null 表示新增 */
  downloader: ConfiguredDownloader | null;
  onSubmit: (payload: DownloaderPayload) => Promise<void>;
  onCancel: () => void;
  onError: (message: string) => void;
}

function DownloaderForm({ downloader, onSubmit, onCancel, onError }: DownloaderFormProps) {
  const [busy, setBusy] = useState(false);
  const [clientType, setClientType] = useState<DownloaderClientType>(
    downloader?.client_type ?? "qbittorrent",
  );
  const [name, setName] = useState(downloader?.name ?? "");
  const [url, setUrl] = useState(downloader?.url ?? "");
  const [username, setUsername] = useState(downloader?.username ?? "");
  // 出于安全后端不回传密码，编辑时留空需重新填写（未开鉴权则保持留空）
  const [password, setPassword] = useState("");
  const [savePath, setSavePath] = useState(downloader?.save_path ?? "");

  const canSubmit = name.trim().length > 0 && /^https?:\/\/.+/.test(url.trim());

  function submit() {
    setBusy(true);
    void onSubmit({
      name: name.trim(),
      client_type: clientType,
      url: url.trim(),
      username: username.trim() || null,
      password: password || null,
      save_path: savePath.trim() || null,
      enabled: downloader?.enabled ?? true,
    })
      .catch((e) => onError((e as Error).message))
      .finally(() => setBusy(false));
  }

  const inputClass =
    "w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] " +
    "text-[var(--text)] outline-none focus:border-[var(--accent)]/60";
  const labelClass = "mb-1.5 block text-xs font-medium text-[var(--text-muted)]";

  return (
    <div className="space-y-4">
      {/* 下载器类型 */}
      <div>
        <label className={labelClass}>下载器类型</label>
        <div className="flex flex-wrap gap-2">
          {(Object.keys(TYPE_LABEL) as DownloaderClientType[]).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setClientType(t)}
              data-active={clientType === t}
              className="glass-row nav-item !w-auto px-3 py-1.5 text-xs font-medium"
            >
              {TYPE_LABEL[t]}
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
          placeholder="如：家里的 qBittorrent"
          autoComplete="off"
          className={inputClass}
        />
      </div>

      <div>
        <label className={labelClass}>
          {clientType === "qbittorrent" ? "WebUI 地址" : "RPC 地址"}
        </label>
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder={URL_PLACEHOLDER[clientType]}
          autoComplete="off"
          className={inputClass}
        />
      </div>

      {/* 凭证：未开鉴权的下载器可整体留空 */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={labelClass}>用户名（可选）</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="off"
            className={inputClass}
          />
        </div>
        <div>
          <label className={labelClass}>密码（可选）</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={downloader ? "出于安全，请重新填写" : ""}
            autoComplete="new-password"
            className={inputClass}
          />
        </div>
      </div>

      <div>
        <label className={labelClass}>默认保存目录（可选）</label>
        <input
          type="text"
          value={savePath}
          onChange={(e) => setSavePath(e.target.value)}
          placeholder="如：/vol1/media/movies"
          autoComplete="off"
          className={inputClass}
        />
        <p className="mt-1.5 text-[11px] leading-relaxed text-[var(--text-faint)]">
          提交下载时文件的保存位置，填<strong className="font-medium text-[var(--text-muted)]">下载器所在机器</strong>上的路径。
          留空则使用下载器自己设置的默认下载目录（如 qBittorrent WebUI 里的「默认保存路径」）。
        </p>
      </div>

      <div className="flex items-center justify-end gap-3 pt-1">
        <button type="button" onClick={onCancel} className="btn-glass px-3.5 py-2 text-xs font-medium">
          取消
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={busy || !canSubmit}
          className="btn-accent rounded-full px-4.5 py-2 text-[13px] font-semibold disabled:opacity-40"
        >
          {busy ? "保存中…" : "保存并测试连接"}
        </button>
      </div>
    </div>
  );
}
