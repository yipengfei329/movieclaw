"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useVirtualizer } from "@tanstack/react-virtual";

import { ExpandIcon, ShrinkIcon } from "@/components/icons";
import { Modal } from "@/components/modal";
import { fetchLogContent, fetchLogDays, type LogContent, type LogDay } from "@/lib/api/logs";
import { formatBytes } from "@/lib/format";

/**
 * 设置页「系统日志」分区：结构化的在线日志查看器。
 *
 * 设计取向参考 Vercel / Railway 的部署日志：不引入自带皮肤的第三方日志组件
 * （react-logviewer / patternfly 的终端风格与本项目玻璃 UI 不搭），而是利用后端
 * 日志本身的结构化格式「时间 | 级别 | 模块 | 内容」自绘行渲染，配合无头虚拟
 * 滚动库 @tanstack/react-virtual 支撑全天数万行日志不卡顿。
 *
 * 能力一览：
 *   - 按行解析出时间 / 级别 / 模块 / 内容四列，级别彩色徽标，异常堆栈等
 *     续行自动归并进上一条日志；
 *   - 级别过滤（全部 / 错误 / 警告 / 信息 / 调试，带条数）+ 关键字搜索高亮；
 *   - 自动刷新：默认 10 秒静默拉取（不闪加载态），可关闭、可选频率，
 *     选择记忆在 localStorage；页面隐藏或查看历史日期时自动暂停；
 *   - tail -f 式跟随：停在底部时新日志自动滚入；向上翻阅则暂停跟随，
 *     右下角浮出「N 条新日志」按钮一键回底。
 */

// ---------------------------------------------------------------------------
// 行解析：后端 core/logging.py 的固定格式「YYYY-MM-DD HH:MM:SS | LEVEL | module | 内容」
// ---------------------------------------------------------------------------

type LogLevel = "ERROR" | "WARNING" | "INFO" | "DEBUG";

interface LogEntry {
  /** 当天内的时刻（HH:MM:SS，日期由所选天数隐含，行内不再重复） */
  time: string;
  level: LogLevel;
  module: string;
  /** 日志正文；异常堆栈等无前缀的续行会以换行归并进来 */
  message: string;
}

const LINE_PATTERN = /^\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}) \| ([A-Z]+) \| (\S+) \| (.*)$/;

/** 把原始行流解析成结构化条目；无法匹配格式的行视为上一条的续行（堆栈等） */
function parseEntries(lines: string[]): LogEntry[] {
  const entries: LogEntry[] = [];
  for (const line of lines) {
    const match = LINE_PATTERN.exec(line);
    if (match) {
      const level = match[2] === "CRITICAL" ? "ERROR" : (match[2] as LogLevel);
      entries.push({
        time: match[1],
        level: ["ERROR", "WARNING", "INFO", "DEBUG"].includes(level) ? level : "INFO",
        module: match[3],
        message: match[4],
      });
    } else if (entries.length > 0) {
      entries[entries.length - 1].message += `\n${line}`;
    } else if (line.trim()) {
      // 文件开头就是续行（tail 截断把堆栈拦腰截断），单独成条兜底展示
      entries.push({ time: "", level: "INFO", module: "", message: line });
    }
  }
  return entries;
}

// ---------------------------------------------------------------------------
// 级别与刷新频率的展示配置
// ---------------------------------------------------------------------------

/** 级别徽标与行着色：错误红 / 警告琥珀 / 信息中性银 / 调试暗淡，贴合冷银主题 */
const LEVEL_STYLE: Record<LogLevel, { badge: string; text: string; row: string }> = {
  ERROR: {
    badge: "bg-[var(--danger)]/15 text-[#ff8a8a]",
    text: "text-[#ffb3b3]",
    row: "bg-[var(--danger)]/[0.05]",
  },
  WARNING: {
    badge: "bg-amber-400/10 text-amber-300/90",
    text: "text-amber-100/80",
    row: "",
  },
  INFO: {
    badge: "bg-white/[0.06] text-[var(--text-muted)]",
    text: "text-[var(--text-muted)]",
    row: "",
  },
  DEBUG: {
    badge: "bg-white/[0.04] text-[var(--text-faint)]",
    text: "text-[var(--text-faint)]",
    row: "",
  },
};

const LEVEL_BADGE_LABEL: Record<LogLevel, string> = {
  ERROR: "ERROR",
  WARNING: "WARN",
  INFO: "INFO",
  DEBUG: "DEBUG",
};

type LevelFilter = "ALL" | LogLevel;

const LEVEL_FILTERS: { id: LevelFilter; label: string }[] = [
  { id: "ALL", label: "全部" },
  { id: "ERROR", label: "错误" },
  { id: "WARNING", label: "警告" },
  { id: "INFO", label: "信息" },
  { id: "DEBUG", label: "调试" },
];

const REFRESH_OPTIONS: { label: string; value: number }[] = [
  { label: "关闭", value: 0 },
  { label: "3s", value: 3_000 },
  { label: "10s", value: 10_000 },
  { label: "30s", value: 30_000 },
];

const REFRESH_STORAGE_KEY = "movieclaw.log-refresh-interval";
const DEFAULT_REFRESH_MS = 10_000;

/** 从 localStorage 恢复自动刷新频率；不可用或值非法时回默认 10 秒 */
function loadRefreshInterval(): number {
  try {
    const stored = localStorage.getItem(REFRESH_STORAGE_KEY);
    // 注意区分「从未设置」和「主动选了关闭(0)」：Number(null) 也是 0，不能混为一谈
    if (stored !== null) {
      const raw = Number(stored);
      if (REFRESH_OPTIONS.some((o) => o.value === raw)) return raw;
    }
  } catch {
    /* localStorage 不可用仅失去记忆能力 */
  }
  return DEFAULT_REFRESH_MS;
}

// ---------------------------------------------------------------------------
// 主组件
// ---------------------------------------------------------------------------

export function SystemLogsSection() {
  const [days, setDays] = useState<LogDay[]>([]);
  const [activeDay, setActiveDay] = useState<string | null>(null);
  const [content, setContent] = useState<LogContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [levelFilter, setLevelFilter] = useState<LevelFilter>("ALL");
  const [query, setQuery] = useState("");
  const [refreshMs, setRefreshMs] = useState(DEFAULT_REFRESH_MS);
  const [pendingNew, setPendingNew] = useState(0);
  /** 全屏观看态：日志窗口经 Modal portal 铺满视口，Esc / 缩小按钮退出 */
  const [fullscreen, setFullscreen] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  /** 是否停在底部（决定新日志是否自动滚入）；翻阅历史时暂停跟随 */
  const atBottomRef = useRef(true);
  /** 静默刷新的在途标记：上一次还没回来就跳过本 tick，避免请求堆积 */
  const inFlightRef = useRef(false);
  /** 「加载全部」后的 tail 口径（0=全量），自动刷新沿用同一口径 */
  const tailRef = useRef<number | undefined>(undefined);

  // 首帧从 localStorage 恢复刷新频率（避免 SSR/水合不一致，放在 effect 里）
  useEffect(() => {
    setRefreshMs(loadRefreshInterval());
  }, []);

  /** 拉取某天内容。silent 为 true 时不动 loading 态（自动刷新不闪屏） */
  const loadDay = useCallback(async (day: string, opts?: { tail?: number; silent?: boolean }) => {
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    if (opts?.tail !== undefined) tailRef.current = opts.tail;
    if (!opts?.silent) {
      setLoading(true);
      setError(null);
    }
    try {
      setContent(await fetchLogContent(day, tailRef.current));
      setError(null);
    } catch (err) {
      if (!opts?.silent) {
        setContent(null);
        setError(err instanceof Error ? err.message : "日志加载失败，请重试");
      }
    } finally {
      inFlightRef.current = false;
      if (!opts?.silent) setLoading(false);
    }
  }, []);

  /** 刷新日期列表；首次进入或手动刷新时自动选中最新一天并加载内容 */
  const refresh = useCallback(
    async (preferDay?: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const list = await fetchLogDays();
        setDays(list);
        const target =
          (preferDay && list.some((d) => d.day === preferDay) && preferDay) ||
          list[0]?.day ||
          null;
        setActiveDay(target);
        if (target) {
          await loadDay(target);
        } else {
          setContent(null);
          setLoading(false);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "日志列表加载失败，请重试");
        setLoading(false);
      }
    },
    [loadDay],
  );

  useEffect(() => {
    void refresh();
  }, [refresh]);

  /** 切换日期：重置 tail 口径与跟随状态，回到「贴底看最新」 */
  const switchDay = useCallback(
    (day: string) => {
      setActiveDay(day);
      tailRef.current = undefined;
      atBottomRef.current = true;
      setPendingNew(0);
      void loadDay(day);
    },
    [loadDay],
  );

  // ---- 自动刷新：仅在查看最新一天时生效；页面隐藏时跳过本 tick ----
  const latestDay = days[0]?.day ?? null;
  const isLatestDay = activeDay !== null && activeDay === latestDay;

  useEffect(() => {
    if (!refreshMs || !activeDay || !isLatestDay) return;
    // 页面隐藏时跳过 tick 省流量；重新可见时立即补一次，不让用户干等下个周期
    const tick = () => {
      if (!document.hidden) void loadDay(activeDay, { silent: true });
    };
    const onVisible = () => {
      if (!document.hidden) tick();
    };
    const id = setInterval(tick, refreshMs);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refreshMs, activeDay, isLatestDay, loadDay]);

  const changeRefresh = useCallback((value: number) => {
    setRefreshMs(value);
    try {
      localStorage.setItem(REFRESH_STORAGE_KEY, String(value));
    } catch {
      /* 忽略 */
    }
  }, []);

  // ---- 解析与过滤 ----
  const entries = useMemo(() => parseEntries(content?.lines ?? []), [content]);

  const levelCounts = useMemo(() => {
    const counts: Record<LogLevel, number> = { ERROR: 0, WARNING: 0, INFO: 0, DEBUG: 0 };
    for (const e of entries) counts[e.level] += 1;
    return counts;
  }, [entries]);

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return entries.filter(
      (e) =>
        (levelFilter === "ALL" || e.level === levelFilter) &&
        (!q || e.message.toLowerCase().includes(q) || e.module.toLowerCase().includes(q)),
    );
  }, [entries, levelFilter, query]);

  // ---- 虚拟滚动：行高不定（消息换行、堆栈多行），用 measureElement 动态测量 ----
  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 24,
    overscan: 20,
  });

  /**
   * 贴底。虚拟滚动的行高靠渲染后动态测量，跳到"底部"后总高度还会继续修正，
   * 一次性 scrollTop = scrollHeight 会落在假底部上；这里小步多轮校正直到收敛。
   */
  const scrollToBottom = useCallback(() => {
    atBottomRef.current = true;
    setPendingNew(0);
    let attempts = 0;
    const settle = () => {
      const el = scrollRef.current;
      if (!el) return;
      if (el.scrollHeight - el.scrollTop - el.clientHeight > 2) {
        el.scrollTop = el.scrollHeight;
      }
      if (attempts++ < 12) setTimeout(settle, 40);
    };
    settle();
  }, []);

  // 全屏切换会把滚动容器整体重挂载：强制虚拟滚动重新测量，并按跟随态回底
  useEffect(() => {
    virtualizer.measure();
    if (atBottomRef.current) scrollToBottom();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fullscreen]);

  // 未贴底时统计静默刷新新进的日志条数（供「N 条新日志」浮标）
  const prevTotalRef = useRef(0);
  useEffect(() => {
    const total = content?.total_lines ?? 0;
    const added = total - prevTotalRef.current;
    prevTotalRef.current = total;
    if (!atBottomRef.current && added > 0) {
      setPendingNew((n) => n + added);
    }
  }, [content]);

  // 可见列表变化后：仍在跟随则自动贴底（首次加载、静默刷新、切过滤都会走到）
  useEffect(() => {
    if (atBottomRef.current) scrollToBottom();
  }, [visible, scrollToBottom]);

  const handleScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
    atBottomRef.current = atBottom;
    if (atBottom) setPendingNew(0);
  }, []);

  const activeMeta = days.find((d) => d.day === activeDay);

  // 工具栏与日志窗口抽成片段：普通态与全屏态（Modal portal）复用同一份 JSX；
  // 同一时刻只渲染其中一处，scrollRef / 虚拟滚动始终只有一个实例
  const toolbar = (
    /* 第一行工具栏：日期 + 元信息 + 自动刷新 + 手动刷新 */
    <div className="flex flex-wrap items-center gap-3">
        <select
          value={activeDay ?? ""}
          disabled={loading || days.length === 0}
          aria-label="选择日志日期"
          onChange={(e) => switchDay(e.target.value)}
          className="rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] text-[var(--text)] outline-none focus:border-[var(--accent)]/60 [&>option]:bg-[#1a1e28]"
        >
          {days.length === 0 && <option value="">暂无日志</option>}
          {days.map((d) => (
            <option key={d.day} value={d.day}>
              {d.day}
            </option>
          ))}
        </select>
        {activeMeta && (
          <span className="tnum text-xs text-[var(--text-muted)]">
            {formatBytes(activeMeta.size_bytes)}
            {content && ` · 共 ${content.total_lines} 行`}
          </span>
        )}
        <div className="flex-1" />

        {/* 自动刷新分段：关闭 / 3s / 10s / 30s；生效时点亮呼吸绿点 */}
        <div className="flex items-center gap-2">
          <span className="flex items-center gap-1.5 text-xs text-[var(--text-muted)]">
            {refreshMs > 0 && isLatestDay && (
              <span className="relative flex size-1.5">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400/60" />
                <span className="relative inline-flex size-1.5 rounded-full bg-emerald-400/90" />
              </span>
            )}
            自动刷新
          </span>
          <div
            role="radiogroup"
            aria-label="自动刷新频率"
            className="flex shrink-0 gap-0.5 rounded-lg bg-white/[0.06] p-0.5"
          >
            {REFRESH_OPTIONS.map((opt) => {
              const active = opt.value === refreshMs;
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => changeRefresh(opt.value)}
                  className={`rounded-md px-2 py-1 text-[12px] font-medium transition-colors ${
                    active
                      ? "bg-white/[0.13] text-[var(--text)]"
                      : "text-[var(--text-muted)] hover:text-[var(--text)]"
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
        </div>

        <button
          type="button"
          onClick={() => void refresh(activeDay)}
          disabled={loading}
          className="btn-glass px-3.5 py-1.5 text-xs font-medium disabled:opacity-40"
        >
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>
  );

  const errorBanner = error ? (
    <p className="rounded-xl border border-[var(--danger)]/30 bg-[var(--danger)]/10 px-4 py-2.5 text-xs text-[var(--danger)]">
      {error}
    </p>
  ) : null;

  // 日志窗口：头部过滤条 + 虚拟滚动内容区；全屏态改为纵向弹性布局撑满面板
  const logWindow = (
    <div
      className={`css-glass overflow-hidden !rounded-2xl ${
        fullscreen ? "flex min-h-0 flex-1 flex-col" : ""
      }`}
    >
        {/* 过滤条：级别 chip（带条数）+ 关键字搜索 */}
        <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.06] bg-white/[0.03] px-3 py-2">
          <div role="radiogroup" aria-label="按级别过滤" className="flex items-center gap-0.5">
            {LEVEL_FILTERS.map((f) => {
              const active = f.id === levelFilter;
              const count = f.id === "ALL" ? entries.length : levelCounts[f.id];
              return (
                <button
                  key={f.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setLevelFilter(f.id)}
                  className={`rounded-full px-2.5 py-[3px] text-[12px] transition-colors ${
                    active
                      ? "bg-white/[0.14] font-medium text-[var(--text)]"
                      : "text-[var(--text-muted)] hover:bg-white/[0.06] hover:text-[var(--text)]"
                  } ${f.id === "ERROR" && count > 0 ? "!text-[#ff8a8a]" : ""}`}
                >
                  {f.label}
                  {count > 0 && <span className="tnum ml-1 opacity-70">{count}</span>}
                </button>
              );
            })}
          </div>
          <div className="flex-1" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索日志…"
            aria-label="搜索日志"
            className="w-40 rounded-lg border border-white/[0.08] bg-white/[0.04] px-2.5 py-1 text-[12px] text-[var(--text)] outline-none placeholder:text-[var(--text-faint)] focus:border-[var(--accent)]/60 sm:w-52"
          />
          <button
            type="button"
            onClick={() => setFullscreen((f) => !f)}
            aria-label={fullscreen ? "退出全屏" : "全屏查看"}
            title={fullscreen ? "退出全屏（Esc）" : "全屏查看"}
            className="rounded-lg p-1.5 text-[var(--text-muted)] transition-colors hover:bg-white/[0.08] hover:text-[var(--text)]"
          >
            {fullscreen ? <ShrinkIcon className="size-4" /> : <ExpandIcon className="size-4" />}
          </button>
        </div>

        {/* 截断提示：默认只取末尾片段，超大日志按需再全量加载 */}
        {content?.truncated && (
          <div className="flex items-center justify-between gap-3 border-b border-white/[0.06] bg-white/[0.02] px-4 py-1.5">
            <p className="text-[11px] text-[var(--text-faint)]">
              日志较长，仅加载末尾 {content.lines.length} 行（全天共 {content.total_lines} 行）
            </p>
            <button
              type="button"
              onClick={() => activeDay && void loadDay(activeDay, { tail: 0 })}
              disabled={loading}
              className="shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-medium text-[var(--text-muted)] transition-colors hover:bg-white/[0.08] hover:text-[var(--text)] disabled:opacity-40"
            >
              加载全部
            </button>
          </div>
        )}

        {/* 内容区：虚拟滚动，行高动态测量 */}
        <div className={fullscreen ? "relative min-h-0 flex-1" : "relative"}>
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className={`scroll-thin overflow-auto bg-black/45 font-mono text-[11.5px] leading-[1.65] ${
              fullscreen ? "h-full" : "h-[62vh] min-h-[18rem]"
            }`}
          >
            {visible.length > 0 ? (
              <div
                className="relative w-full"
                style={{ height: virtualizer.getTotalSize() }}
              >
                {virtualizer.getVirtualItems().map((vi) => {
                  const entry = visible[vi.index];
                  return (
                    <div
                      key={vi.key}
                      data-index={vi.index}
                      ref={virtualizer.measureElement}
                      className="absolute left-0 top-0 w-full"
                      style={{ transform: `translateY(${vi.start}px)` }}
                    >
                      <LogRow entry={entry} query={query.trim()} />
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="px-6 py-12 text-center font-sans text-xs text-[var(--text-faint)]">
                {loading
                  ? "日志加载中…"
                  : days.length === 0
                    ? "还没有任何日志文件。日志随后端运行按天写入服务端 data/logs 目录，稍后再来看看。"
                    : entries.length === 0
                      ? "这一天的日志是空的。"
                      : "没有匹配的日志，换个级别或关键字试试。"}
              </p>
            )}
          </div>

          {/* 翻阅历史时新日志到达 → 右下角浮标一键回底恢复跟随 */}
          {pendingNew > 0 && (
            <button
              type="button"
              onClick={scrollToBottom}
              className="btn-glass absolute bottom-4 right-4 flex items-center gap-1.5 !rounded-full px-3.5 py-1.5 text-xs font-medium shadow-lg"
            >
              <span aria-hidden>↓</span>
              {pendingNew} 条新日志
            </button>
          )}
        </div>
      </div>
  );

  // 全屏观看态：经 Modal portal 到 body 的沉浸层（fixed 不会被 backdrop-filter
  // 祖先困住），复用同一份工具栏与日志窗口，Esc / 点遮罩 / 缩小按钮退出
  if (fullscreen) {
    return (
      <Modal
        open
        onClose={() => setFullscreen(false)}
        label="系统日志（全屏）"
        width="full"
        panelClassName="h-full"
      >
        <div className="flex h-full flex-col gap-4 p-5">
          {toolbar}
          {errorBanner}
          {logWindow}
        </div>
      </Modal>
    );
  }

  return (
    <div className="space-y-4">
      {toolbar}
      {errorBanner}
      {logWindow}

      <div className="flex items-start justify-between gap-4">
        <p className="text-on-image text-xs leading-5 text-[var(--text-faint)]">
          日志按天存档在服务端 data/logs 目录（Docker 部署挂载 data 卷即可持久化），
          超过保留天数的旧日志会自动清理；保留天数与目录位置可通过 LOG_RETENTION_DAYS、LOG_DIR
          环境变量调整。
        </p>
        {!isLatestDay && activeDay && refreshMs > 0 && (
          <p className="shrink-0 text-xs text-[var(--text-faint)]">正在查看历史日志，自动刷新已暂停</p>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 单行渲染
// ---------------------------------------------------------------------------

/** 一行日志：时间（暗淡等宽）+ 级别徽标 + 模块（银蓝）+ 正文（可换行、命中高亮） */
function LogRow({ entry, query }: { entry: LogEntry; query: string }) {
  const style = LEVEL_STYLE[entry.level];
  return (
    <div className={`flex items-start gap-2.5 px-4 py-[3px] transition-colors hover:bg-white/[0.04] ${style.row}`}>
      <span className="tnum shrink-0 select-none text-[var(--text-faint)]">{entry.time || "​"}</span>
      <span
        className={`mt-[2px] w-[46px] shrink-0 select-none rounded px-1 py-px text-center text-[9.5px] font-semibold tracking-wide ${style.badge}`}
      >
        {LEVEL_BADGE_LABEL[entry.level]}
      </span>
      <span
        className="hidden max-w-[190px] shrink-0 truncate text-[var(--accent-2)]/80 sm:block"
        title={entry.module}
      >
        {entry.module}
      </span>
      <span className={`min-w-0 flex-1 whitespace-pre-wrap break-all ${style.text}`}>
        {highlightMatches(entry.message, query)}
      </span>
    </div>
  );
}

/** 搜索命中高亮：按关键字大小写不敏感切分，命中片段用浅银底标出 */
function highlightMatches(text: string, query: string): React.ReactNode {
  if (!query) return text;
  const lower = text.toLowerCase();
  const q = query.toLowerCase();
  const parts: React.ReactNode[] = [];
  let cursor = 0;
  let hit = lower.indexOf(q);
  while (hit >= 0) {
    if (hit > cursor) parts.push(text.slice(cursor, hit));
    parts.push(
      <mark
        key={hit}
        className="rounded-sm bg-[var(--accent)]/30 px-px text-[var(--text)]"
      >
        {text.slice(hit, hit + query.length)}
      </mark>,
    );
    cursor = hit + query.length;
    hit = lower.indexOf(q, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}
