"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { fetchLogContent, fetchLogDays, type LogContent, type LogDay } from "@/lib/api/logs";
import { formatBytes } from "@/lib/format";

/**
 * 设置页「系统日志」分区：在线查看后端按天落盘的运行日志。
 *
 * 数据来源是后端 /system/logs 接口（读取 data/logs/movieclaw-YYYY-MM-DD.log）：
 *   - 顶部工具栏：日期下拉（倒序，最新在前）+ 文件大小 + 刷新按钮；
 *   - 日志窗口：等宽字体、深色滚动区，按行级着色（ERROR 红 / WARNING 黄），
 *     加载后自动滚到底部（最新日志在末尾，贴近 tail -f 的阅读习惯）；
 *   - 默认只拉当天末尾 2000 行防止超大文件卡住页面，被截断时可点「加载全部」。
 */
export function SystemLogsSection() {
  const [days, setDays] = useState<LogDay[]>([]);
  const [activeDay, setActiveDay] = useState<string | null>(null);
  const [content, setContent] = useState<LogContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  /** 拉取某天的内容；tail=0 表示全量（「加载全部」按钮） */
  const loadDay = useCallback(async (day: string, tail?: number) => {
    setLoading(true);
    setError(null);
    try {
      setContent(await fetchLogContent(day, tail));
    } catch (err) {
      setContent(null);
      setError(err instanceof Error ? err.message : "日志加载失败，请重试");
    } finally {
      setLoading(false);
    }
  }, []);

  /** 刷新日期列表；首次进入或刷新时自动选中最新一天并加载内容 */
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

  // 内容更新后滚到底部：日志按时间正序写入，最新的在末尾
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [content]);

  const activeMeta = days.find((d) => d.day === activeDay);

  return (
    <div className="space-y-4">
      {/* 工具栏：日期切换 + 当天文件大小 + 刷新 */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={activeDay ?? ""}
          disabled={loading || days.length === 0}
          aria-label="选择日志日期"
          onChange={(e) => {
            setActiveDay(e.target.value);
            void loadDay(e.target.value);
          }}
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
        <button
          type="button"
          onClick={() => void refresh(activeDay)}
          disabled={loading}
          className="btn-glass px-3.5 py-1.5 text-xs font-medium disabled:opacity-40"
        >
          {loading ? "加载中…" : "刷新"}
        </button>
      </div>

      {error && (
        <p className="rounded-xl border border-[var(--danger)]/30 bg-[var(--danger)]/10 px-4 py-2.5 text-xs text-[var(--danger)]">
          {error}
        </p>
      )}

      {/* 截断提示：默认只取末尾片段，超大日志按需再全量加载 */}
      {content?.truncated && (
        <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-white/[0.04] px-4 py-2.5">
          <p className="text-xs text-[var(--text-muted)]">
            日志较长，仅显示末尾 {content.lines.length} 行（全天共 {content.total_lines} 行）
          </p>
          <button
            type="button"
            onClick={() => activeDay && void loadDay(activeDay, 0)}
            disabled={loading}
            className="btn-glass shrink-0 px-3 py-1 text-xs font-medium disabled:opacity-40"
          >
            加载全部
          </button>
        </div>
      )}

      {/* 日志窗口：深色等宽滚动区，按行级着色 */}
      <div className="css-glass overflow-hidden !rounded-2xl">
        <div
          ref={scrollRef}
          className="scroll-thin max-h-[62vh] min-h-[16rem] overflow-auto bg-black/45 px-4 py-3 font-mono text-[11.5px] leading-[1.7]"
        >
          {content && content.lines.length > 0 ? (
            content.lines.map((line, i) => (
              <div key={i} className={`whitespace-pre-wrap break-all ${lineColor(line)}`}>
                {line}
              </div>
            ))
          ) : (
            <p className="py-10 text-center text-xs text-[var(--text-faint)]">
              {loading
                ? "日志加载中…"
                : days.length === 0
                  ? "还没有任何日志文件。日志随后端运行按天写入服务端 data/logs 目录，稍后再来看看。"
                  : "这一天的日志是空的。"}
            </p>
          )}
        </div>
      </div>

      <p className="text-on-image text-xs leading-5 text-[var(--text-faint)]">
        日志按天存档在服务端 data/logs 目录（Docker 部署挂载 data 卷即可持久化），
        超过保留天数的旧日志会自动清理；保留天数与目录位置可通过 LOG_RETENTION_DAYS、LOG_DIR
        环境变量调整。
      </p>
    </div>
  );
}

/** 按日志级别给整行着色：格式为「时间 | 级别 | 模块 | 内容」（见后端 core/logging.py） */
function lineColor(line: string): string {
  if (line.includes(" | ERROR | ") || line.includes(" | CRITICAL | ")) {
    return "text-[var(--danger)]";
  }
  if (line.includes(" | WARNING | ")) {
    return "text-amber-300/90";
  }
  if (line.includes(" | DEBUG | ")) {
    return "text-[var(--text-faint)]";
  }
  return "text-[var(--text-muted)]";
}
