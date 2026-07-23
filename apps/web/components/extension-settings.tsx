"use client";

import { useCallback, useEffect, useState } from "react";

import { CheckIcon, ClockIcon, CopyIcon, DownloadIcon, PuzzleIcon, ShieldIcon } from "@/components/icons";
import { EXTENSION_ZIP_URL, useExtensionInstalled } from "@/lib/extension-install";
import {
  type ConfiguredSite,
  type SiteStatus,
  type SyncTokenView,
  generateSyncToken,
  getSyncToken,
  listConfiguredSites,
  revokeSyncToken,
} from "@/lib/api/extension";
import { formatDateTime, formatRelativeTime } from "@/lib/time";

/** 站点验证状态 → 展示文案与颜色 */
const STATUS_META: Record<SiteStatus, { label: string; color: string }> = {
  active: { label: "已验证", color: "#4ade80" },
  verifying: { label: "验证中", color: "#6aa7ff" },
  pending: { label: "待验证", color: "#c0c4cc" },
  failed: { label: "失败", color: "#ff6b6b" },
};

/**
 * 「浏览器插件」设置分区：
 * - 顶部：MovieClaw 插件的安装引导——自动检测是否已安装（见 lib/extension-install.ts），
 *   未安装时提供 zip 下载与加载步骤（Chrome 政策不允许商店外插件一键静默安装，
 *   下载后需在 chrome://extensions 手动加载，页面把步骤讲清楚）；
 * - 中部：同步令牌的生成 / 查看 / 复制 / 重新生成 / 关闭；
 * - 下半部：最近活动——插件同步管理的 cookie 站点及其验证状态与时间。
 */
export function ExtensionSection() {
  const [token, setToken] = useState<SyncTokenView | null>(null);
  const [sites, setSites] = useState<ConfiguredSite[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [tokenView, allSites] = await Promise.all([getSyncToken(), listConfiguredSites()]);
      setToken(tokenView);
      // 只展示 cookie 授权的站点——那才是浏览器插件同步的范畴
      setSites(allSites.filter((s) => s.auth_type === "cookie"));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function onGenerate() {
    if (token?.enabled && !window.confirm("重新生成将使旧令牌立即失效，已配置的插件需要更新令牌。继续？")) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setToken(await generateSyncToken());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke() {
    if (!window.confirm("关闭同步将撤销令牌，所有插件都将无法再同步。继续？")) return;
    setBusy(true);
    setError(null);
    try {
      setToken(await revokeSyncToken());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      {error && (
        <div className="rounded-xl border border-[#ff6b6b]/30 bg-[#ff6b6b]/10 px-4 py-3 text-sm text-[#ff6b6b]">
          {error}
        </div>
      )}

      {/* —— 安装插件 —— */}
      <InstallCard />

      {/* —— 同步令牌 —— */}
      <section className="css-glass !rounded-2xl p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3.5">
            <span className="icon-chip size-10 shrink-0 !rounded-xl">
              <ShieldIcon className="size-5" />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold">同步令牌</h2>
              <p className="mt-0.5 text-xs leading-5 text-[var(--text-muted)]">
                在浏览器插件的设置里填入此令牌，即可把站点 Cookie 同步到本服务。令牌长期有效，除非你重新生成。
              </p>
            </div>
          </div>
          <StatusDot on={Boolean(token?.enabled)} />
        </div>

        <div className="mt-4">
          {loading ? (
            <div className="h-11 animate-pulse rounded-xl bg-white/[0.04]" />
          ) : token?.enabled ? (
            <TokenRow token={token.token ?? ""} createdAt={token.created_at} />
          ) : (
            <p className="rounded-xl bg-white/[0.03] px-4 py-3 text-sm text-[var(--text-muted)]">
              尚未启用同步。点击下方「生成令牌」创建一个。
            </p>
          )}
        </div>

        <div className="mt-5 flex flex-wrap gap-3">
          <button
            type="button"
            onClick={onGenerate}
            disabled={busy}
            className="btn-accent rounded-full px-4 py-2 text-xs font-semibold disabled:opacity-60"
          >
            {token?.enabled ? "重新生成" : "生成令牌"}
          </button>
          {token?.enabled && (
            <button
              type="button"
              onClick={onRevoke}
              disabled={busy}
              className="btn-glass px-4 py-2 text-xs font-medium !text-[var(--danger)] hover:!border-[#ff6b6b]/40"
            >
              关闭同步
            </button>
          )}
        </div>
      </section>

      {/* —— 最近活动 —— */}
      <section className="css-glass !rounded-2xl p-6">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-start gap-3.5">
            <span className="icon-chip size-10 shrink-0 !rounded-xl">
              <ClockIcon className="size-5" />
            </span>
            <div>
              <h2 className="text-[15px] font-semibold">最近活动</h2>
              <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                通过插件同步 Cookie 的站点及其验证状态。
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="btn-glass shrink-0 px-3.5 py-1.5 text-xs font-medium"
          >
            刷新
          </button>
        </div>

        <div className="mt-4 space-y-1.5">
          {loading ? (
            <>
              <div className="h-14 animate-pulse rounded-xl bg-white/[0.04]" />
              <div className="h-14 animate-pulse rounded-xl bg-white/[0.04]" />
            </>
          ) : sites.length === 0 ? (
            <p className="rounded-xl bg-white/[0.03] px-4 py-6 text-center text-sm text-[var(--text-muted)]">
              还没有通过插件同步的站点。在支持的站点页面用插件点「同步」即可。
            </p>
          ) : (
            sites.map((site) => <ActivityRow key={site.site_id} site={site} />)
          )}
        </div>
      </section>
    </div>
  );
}

/* —— 子组件 —— */

/**
 * 安装引导卡：自动检测 MovieClaw 插件是否已安装。
 * - 已安装：绿色徽标 + 一句使用指引，不再打扰；
 * - 未安装：下载按钮 + 三步加载指引。Chrome 不允许网页静默安装商店外插件，
 *   「下载 → 解压 → 加载」是自部署场景下最短的路径；装完切回本页自动变绿
 *   （useExtensionInstalled 在窗口重获焦点时复测）。
 */
function InstallCard() {
  const { installed } = useExtensionInstalled();

  const badge =
    installed === null
      ? { label: "检测中…", color: "#c0c4cc" }
      : installed
        ? { label: "已安装", color: "#4ade80" }
        : { label: "未检测到", color: "#c0c4cc" };

  return (
    <section className="css-glass !rounded-2xl p-6">
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3.5">
          <span className="icon-chip size-10 shrink-0 !rounded-xl">
            <PuzzleIcon className="size-5" />
          </span>
          <div>
            <h2 className="text-[15px] font-semibold">MovieClaw 浏览器插件</h2>
            <p className="mt-0.5 text-xs leading-5 text-[var(--text-muted)]">
              在站点页面一键读取登录 Cookie（含 httpOnly）并同步到本服务，免去手动复制粘贴，
              还能随 Cookie 变化自动保持最新。
            </p>
          </div>
        </div>
        <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--text-muted)]">
          <span
            className={`size-2 rounded-full ${installed === null ? "animate-pulse" : ""}`}
            style={{ background: badge.color }}
          />
          {badge.label}
        </span>
      </div>

      {installed ? (
        <p className="mt-4 rounded-xl bg-white/[0.03] px-4 py-3 text-sm text-[var(--text-muted)]">
          <CheckIcon className="mr-1.5 inline size-4 text-[#4ade80]" />
          插件已就绪。打开支持的站点页面，点击浏览器工具栏的 MovieClaw
          图标即可同步该站 Cookie；令牌配置见下方。
        </p>
      ) : (
        <>
          <ol className="mt-4 space-y-2 rounded-xl bg-white/[0.03] px-4 py-3.5 text-[13px] leading-6 text-[var(--text-muted)]">
            <li>
              <b className="text-[var(--text)]">1.</b> 点击下方按钮下载插件包，解压得到{" "}
              <code className="rounded bg-white/[0.06] px-1 font-mono text-xs">chrome-mv3</code> 文件夹。
            </li>
            <li>
              <b className="text-[var(--text)]">2.</b> 浏览器打开{" "}
              <code className="rounded bg-white/[0.06] px-1 font-mono text-xs">chrome://extensions</code>
              ，右上角开启「开发者模式」。
            </li>
            <li>
              <b className="text-[var(--text)]">3.</b>{" "}
              点「加载已解压的扩展程序」选择该文件夹，切回本页即自动识别为「已安装」。
            </li>
          </ol>
          <div className="mt-4 flex flex-wrap items-center gap-3">
            <a
              href={EXTENSION_ZIP_URL}
              download
              className="btn-accent flex items-center gap-1.5 rounded-full px-4 py-2 text-xs font-semibold"
            >
              <DownloadIcon className="size-4" />
              下载插件包
            </a>
            <p className="text-[11px] text-[var(--text-faint)]">
              支持 Chrome / Edge 等 Chromium 内核浏览器；安装检测同样仅对 Chromium 生效。
            </p>
          </div>
        </>
      )}
    </section>
  );
}

function StatusDot({ on }: { on: boolean }) {
  return (
    <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--text-muted)]">
      <span
        className="size-2 rounded-full"
        style={{ background: on ? "#4ade80" : "#c0c4cc" }}
      />
      {on ? "已启用" : "未启用"}
    </span>
  );
}

function TokenRow({ token, createdAt }: { token: string; createdAt: string | null }) {
  const [revealed, setRevealed] = useState(false);
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 忽略：某些环境无剪贴板权限，用户可手动选择复制 */
    }
  }

  const display = revealed ? token : "•".repeat(Math.min(token.length, 28));

  return (
    <div>
      <div className="flex items-center gap-2 rounded-xl bg-white/[0.04] px-3 py-2.5">
        <code className="min-w-0 flex-1 truncate font-mono text-[13px] text-[var(--text)]">
          {display}
        </code>
        <button
          type="button"
          onClick={() => setRevealed((v) => !v)}
          className="btn-glass shrink-0 px-2.5 py-1 text-xs font-medium"
        >
          {revealed ? "隐藏" : "显示"}
        </button>
        <button
          type="button"
          onClick={copy}
          aria-label="复制令牌"
          className="btn-glass shrink-0 px-2 py-1 text-xs font-medium"
        >
          {copied ? <CheckIcon className="size-4 text-[#4ade80]" /> : <CopyIcon className="size-4" />}
        </button>
      </div>
      {createdAt && (
        <p className="mt-1.5 text-[11px] text-[var(--text-faint)]">
          生成于 {formatDateTime(createdAt)}
        </p>
      )}
    </div>
  );
}

function ActivityRow({ site }: { site: ConfiguredSite }) {
  const meta = STATUS_META[site.status];
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl bg-white/[0.03] px-4 py-3">
      <div className="min-w-0">
        <p className="truncate text-sm font-medium text-[var(--text)]">{site.site_id}</p>
        <p className="truncate text-[11px] text-[var(--text-faint)]">
          {site.status === "failed" && site.last_error
            ? site.last_error
            : `上次检查：${formatRelativeTime(site.last_checked_at)}`}
        </p>
      </div>
      <span
        className="flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium"
        style={{ background: `${meta.color}1f`, color: meta.color }}
      >
        <span className="size-1.5 rounded-full" style={{ background: meta.color }} />
        {meta.label}
      </span>
    </div>
  );
}
