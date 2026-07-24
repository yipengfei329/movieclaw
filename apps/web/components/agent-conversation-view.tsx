"use client";

import { useEffect, useRef, useState } from "react";

import { Composer } from "@/components/composer";
import { HighlightedCode } from "@/components/highlighted-code";
import { LlmSetupNotice, useLlmConfigured } from "@/components/llm-gate";
import { ChevronRightIcon, SparkIcon } from "@/components/icons";
import { Markdown } from "@/components/markdown";
import type { CodeLang } from "@/lib/shiki";
import {
  type AgentProcessItem,
  type AgentTurn,
  type AgentTurnSegment,
  type AgentTurnToolCall,
  useAgentConversations,
} from "@/lib/agent-conversations";
import { usePageTitle } from "@/lib/use-page-title";

/**
 * Agent 会话页（/runs/[id]）—— 仿 ChatGPT 的对话交互：
 * 顶部标题条 + 可滚动消息列（用户右侧气泡 / Agent 左侧全宽块）+ 底部固定
 * Composer。流式生成中支持停止；随时可追问下一轮（自动携带多轮历史）。
 */
export function AgentConversationView({ conversationId }: { conversationId: string }) {
  const { get, open, send, stop } = useAgentConversations();
  const conversation = get(conversationId);
  usePageTitle(conversation?.title);
  const [input, setInput] = useState("");
  // 服务端详情加载失败的提示（404 = 会话不存在；其余为网络/服务错误）
  const [loadError, setLoadError] = useState<string | null>(null);
  // 供应商被删除后打开旧会话：追问同样锁定并引导去设置（false = 明确未配置）
  const locked = useLlmConfigured() === false;

  // 打开会话：详情未加载时从服务端回放（running 会话同时重挂事件流）
  useEffect(() => {
    setLoadError(null);
    open(conversationId).catch((error) => {
      setLoadError((error as Error).message);
    });
  }, [conversationId, open]);

  // 自动滚动：仅当用户本就贴近底部时跟随新内容（ChatGPT 同款行为——
  // 用户上滚查看历史时不打断）
  const scrollRef = useRef<HTMLDivElement>(null);
  const nearBottomRef = useRef(true);
  useEffect(() => {
    const el = scrollRef.current;
    if (el && nearBottomRef.current) el.scrollTop = el.scrollHeight;
  }, [conversation]);

  if (loadError) {
    return (
      <div className="immersive-theme flex h-full items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <p className="text-sm font-medium text-[var(--text)]">无法打开会话</p>
          <p className="mt-2 text-xs leading-5 text-[var(--text-muted)]">{loadError}</p>
        </div>
      </div>
    );
  }

  if (!conversation?.loaded) {
    return (
      <div className="immersive-theme flex h-full items-center justify-center px-6">
        <p className="animate-pulse text-sm text-[var(--text-muted)]">正在加载会话…</p>
      </div>
    );
  }

  const running = conversation.turns.some((t) => t.status === "running");

  function submit(text: string) {
    setInput("");
    send(conversationId, text);
  }

  return (
    // 沉浸页：内容直接铺在页面纯色底（.page-solid）上，不再包阅读面板卡片；
    // immersive-theme 在容器内切换为中性灰阶 + 系统字体的阅读配色
    <div className="immersive-theme flex h-full flex-col">
      {/* 顶部条：会话标题 + 运行状态 */}
      <header className="flex h-14 shrink-0 items-center px-5">
        <div className="flex min-w-0 items-center gap-2.5">
          <h1 className="truncate text-[14px] font-semibold tracking-[-0.01em]">
            {conversation.title}
          </h1>
          <span className="flex shrink-0 items-center gap-1.5 rounded-full bg-white/[0.06] px-2.5 py-0.5 text-[11px] text-[var(--text-muted)]">
            <span
              className={`size-1.5 rounded-full ${running ? "animate-pulse bg-[#6aa7ff]" : "bg-[#4ade80]"}`}
            />
            {running ? "生成中" : "就绪"}
          </span>
        </div>
      </header>

      {/* 消息列 */}
      <div
        ref={scrollRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
        }}
        className="scroll-thin min-h-0 flex-1 overflow-y-auto px-6 py-6"
      >
        <div className="mx-auto max-w-3xl space-y-6">
          {conversation.turns.map((turn) => (
            <TurnView key={turn.id} turn={turn} />
          ))}
        </div>
      </div>

      {/* 底部输入：生成中可继续打字，发送键变停止键 */}
      <div className="shrink-0 px-4 pb-5 pt-2">
        <div className="mx-auto max-w-3xl">
          <Composer
            flat
            value={input}
            onChange={setInput}
            onSubmit={submit}
            busy={running}
            onStop={() => stop(conversationId)}
            disabled={locked}
            placeholder={locked ? "请先接入 AI 模型，再继续对话" : undefined}
          />
          {locked && <LlmSetupNotice />}
        </div>
      </div>
    </div>
  );
}

/* —— 单轮：用户气泡 + Agent 回应块 —— */

function TurnView({ turn }: { turn: AgentTurn }) {
  return (
    <div className="space-y-4">
      {/* 用户消息：右侧玻璃气泡 */}
      <div className="flex justify-end">
        <div className="max-w-[80%] whitespace-pre-wrap rounded-2xl bg-[var(--glass-fill-active)] px-4 py-3 text-sm leading-6 text-[var(--text)]">
          {turn.input}
        </div>
      </div>

      {/* Agent 回应：左侧全宽块（ChatGPT 式，不用窄气泡） */}
      <div className="flex gap-3">
        <span className="icon-chip mt-0.5 flex size-7 shrink-0 items-center justify-center !rounded-lg">
          <SparkIcon
            className={`size-4 ${
              turn.status === "running"
                ? "animate-pulse text-[var(--accent-2)]"
                : turn.status === "error"
                  ? "text-[#ff6b6b]"
                  : "text-[var(--text-muted)]"
            }`}
          />
        </span>
        <div className="min-w-0 flex-1 space-y-2.5 pt-1">
          {turn.segments.map((segment, index) => {
            // 时间线按序渲染：process 折叠块与正文交替出现
            const isLast = index === turn.segments.length - 1;
            const active = turn.status === "running" && isLast;
            return segment.kind === "process" ? (
              <ProcessBlock key={index} segment={segment} active={active} />
            ) : (
              <div key={index}>
                <Markdown text={segment.text} />
                {active && <StreamingCursor />}
              </div>
            );
          })}

          {turn.status === "running" && turn.segments.length === 0 && (
            <p className="text-[13px] text-[var(--text-faint)]">
              正在启动
              <StreamingCursor />
            </p>
          )}

          {turn.status === "error" && (
            <div className="rounded-xl border border-[#ff6b6b]/30 bg-[#ff6b6b]/10 px-3.5 py-2.5 text-[13px] leading-5 text-[#ff6b6b]">
              {turn.error}
            </div>
          )}

          {turn.status === "done" && (
            <p className="text-[11px] text-[var(--text-faint)]">
              {turn.stopped
                ? "已停止生成"
                : turn.result &&
                  `${turn.provider} · ${turn.model}${turn.result.steps > 1 ? ` · ${turn.result.steps} 步` : ""} · ${turn.result.usage.prompt_tokens}/${turn.result.usage.completion_tokens} tokens · ${(turn.result.elapsed_ms / 1000).toFixed(1)}s`}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/** 进行中的处理块此刻在做什么：取末尾条目推导实时状态。 */
function processStatus(items: AgentProcessItem[]): string {
  const tail = items[items.length - 1];
  if (!tail || tail.kind === "thinking") return "思考中…";
  if (tail.argsDone === false) return `准备调用 ${tail.name}…`;
  if (tail.output === undefined) return `正在执行 ${tail.name}…`;
  return "思考中…"; // 工具已返回，正在等模型的下一步
}

/** 已完成处理块的一句话总结，如「已思考，执行 1 次命令，调用 2 次工具」。 */
function processSummary(items: AgentProcessItem[]): string {
  const tools = items.filter((item) => item.kind === "tool");
  const commands = tools.filter((tool) => tool.name === "bash").length;
  const others = tools.length - commands;
  const parts: string[] = [];
  if (items.some((item) => item.kind === "thinking")) parts.push("思考");
  if (commands > 0) parts.push(`执行 ${commands} 次命令`);
  if (others > 0) parts.push(`调用 ${others} 次工具`);
  return parts.length > 0 ? `已${parts.join("，")}` : "处理过程";
}

/**
 * 处理过程折叠块（仿 Claude）：头部进行中显示实时状态、完成后显示一句话
 * 总结；点击展开思考与工具调用的混合列表（按实际发生顺序）。
 */
function ProcessBlock({ segment, active }: { segment: AgentTurnSegment & { kind: "process" }; active: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1 text-[12px] font-medium text-[var(--text-faint)] transition-colors hover:text-[var(--text-muted)]"
      >
        <ChevronRightIcon className={`size-3.5 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className={active ? "animate-pulse" : undefined}>
          {active ? processStatus(segment.items) : processSummary(segment.items)}
        </span>
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 border-l-2 border-white/[0.08] pl-3">
          {segment.items.map((item, index) =>
            item.kind === "thinking" ? (
              <p
                key={index}
                className="whitespace-pre-wrap text-[12px] leading-5 text-[var(--text-faint)]"
              >
                {item.text}
              </p>
            ) : (
              <ToolCallCard key={item.id} tool={item} />
            ),
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 从定稿的 label（形如 `name({...json...})`）还原参数明细：
 * bash 直接取出 command 按 shell 展示，其余工具美化 JSON。
 * 解析失败（历史数据/异常参数）时原样展示括号内文本。
 */
function toolInput(tool: AgentTurnToolCall): { lang: CodeLang; code: string } | null {
  const raw = tool.label.slice(tool.name.length + 1, -1);
  if (!raw || raw === "{}") return null;
  try {
    const args = JSON.parse(raw) as Record<string, unknown>;
    if (tool.name === "bash" && typeof args.command === "string") {
      return { lang: "bash", code: args.command };
    }
    return { lang: "json", code: JSON.stringify(args, null, 2) };
  } catch {
    return { lang: "json", code: raw };
  }
}

/** 单次工具调用卡片：参数完整展示（shiki 高亮）+ 生成/执行中/回执三种状态。 */
function ToolCallCard({ tool }: { tool: AgentTurnToolCall }) {
  return (
    <div className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-2.5 py-1.5">
      {tool.argsDone === false ? (
        // 参数生成中：工具名固定，参数区右锚定滚动显示最新生成的尾部——
        // 长参数溢出时新字符持续从右侧推入，肉眼可见任务仍在进行
        <p className="flex font-mono text-[11px] text-[var(--accent-2)]">
          <span className="shrink-0">⚙ {tool.name}(</span>
          <span className="flex min-w-0 flex-1 justify-end overflow-hidden [mask-image:linear-gradient(to_right,transparent,black_24px)]">
            <span className="whitespace-nowrap">
              {tool.label.slice(tool.name.length + 1).slice(-300)}
            </span>
          </span>
        </p>
      ) : (
        <>
          <p className="font-mono text-[11px] text-[var(--accent-2)]">⚙ {tool.name}</p>
          {(() => {
            const input = toolInput(tool);
            return input && <HighlightedCode code={input.code} lang={input.lang} />;
          })()}
        </>
      )}
      {tool.argsDone === false ? (
        <p className="mt-0.5 animate-pulse text-[11px] text-[var(--text-faint)]">生成参数中…</p>
      ) : tool.output === undefined ? (
        <p className="mt-0.5 text-[11px] text-[var(--text-faint)]">执行中…</p>
      ) : (
        <div
          className={`scroll-thin mt-1 max-h-44 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-4 ${
            tool.isError ? "text-[#ff6b6b]" : "text-[var(--text-muted)]"
          }`}
        >
          {tool.isError ? "✗ " : "✓ "}
          {tool.output}
        </div>
      )}
    </div>
  );
}

/** 流式光标：正文尾部的呼吸圆点。 */
function StreamingCursor() {
  return (
    <span className="ml-0.5 inline-block size-[13px] translate-y-[2px] animate-pulse rounded-full bg-[var(--text-muted)]" />
  );
}
