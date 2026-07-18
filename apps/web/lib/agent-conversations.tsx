"use client";

/**
 * Agent 会话的全站客户端状态（服务端会话模型）。
 *
 * 事实源在服务端（JSONL 转录 + SQLite 索引），本 store 只是它的渲染缓存：
 * - 会话列表来自 GET /agent/sessions（仅摘要，turns 为空的「未加载」壳）；
 * - 打开某会话时用详情接口把 entries 回放成 AgentTurn 时间线；
 * - 新建 / 续聊只带 session_id，历史由服务端从转录重建，不再回传 history；
 * - running 的会话用 active_run_id 重新挂上 SSE：正在运行那一轮丢弃已落盘
 *   的局部产出、从事件 0 完整回放，避免转录快照与事件游标不同步造成重复。
 *
 * 后台运行与 SSE 连接保持解耦：store 先取得 runId，再单独订阅事件；网络
 * 中断由 SSE 客户端按事件序号续传，关闭页面也不会取消后端任务。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  type AgentDone,
  type AgentEvent,
  type AgentSessionDetail,
  type AgentSessionEntry,
  type AgentSessionSummary,
  type AgentTranscriptMessage,
  cancelAgentRun,
  deleteAgentSession,
  getAgentSession,
  listAgentSessions,
  renameAgentSession,
  startAgentRun,
  streamAgentRun,
} from "@/lib/api/agent";
import { HttpError } from "@/lib/http";

/** 一次工具调用及其执行回执（tool_call_start 创建、tool_call_delta 逐片
 * 追加参数、tool_call 定稿参数、tool_result 补全回执）。 */
export interface AgentTurnToolCall {
  id: string;
  /** 工具名，如 write / bash；处理过程块的状态与总结按它分类 */
  name: string;
  /** 展示用摘要，如 search({"q":"沙丘"})；参数生成中为逐片追加的半成品 */
  label: string;
  /** 参数是否已生成完整（tool_call 事件到达）；undefined 视为已完整（回放数据） */
  argsDone?: boolean;
  /** 执行回执（未返回时为 undefined = 执行中） */
  output?: string;
  isError?: boolean;
  elapsedMs?: number;
}

/** 处理过程条目：一段思维链或一次工具调用，按实际发生顺序排列。 */
export type AgentProcessItem =
  | { kind: "thinking"; text: string }
  | ({ kind: "tool" } & AgentTurnToolCall);

/**
 * 时间线段（仿 Claude 的呈现模型）：
 * - process：连续的思考/工具活动折叠为一个「处理过程」块；
 * - text：模型的正文输出。
 * agent loop 中两者交替出现（每步：思考/调工具 → 可能穿插正文 → 下一步）。
 */
export type AgentTurnSegment =
  | { kind: "process"; items: AgentProcessItem[] }
  | { kind: "text"; text: string };

/** 一轮对话：用户输入 + Agent 的完整产出。 */
export interface AgentTurn {
  id: string;
  /** 后端异步运行编号；创建接口成功后写入，重连和取消都依赖它。 */
  runId?: string;
  input: string;
  status: "running" | "done" | "error";
  /** agent_start 事件带回的实际路由信息 */
  provider?: string;
  model?: string;
  /** 按发生顺序排列的产出时间线（处理过程块与正文交替） */
  segments: AgentTurnSegment[];
  result?: AgentDone;
  error?: string;
  /** 用户主动停止：status 为 done 但结果不完整 */
  stopped?: boolean;
}

export interface AgentConversation {
  /** 服务端会话 id（路由 /runs/[id] 与续聊请求都用它） */
  id: string;
  title: string;
  updatedAt: number;
  /** 是否有存活的运行（列表摘要派生；详情加载后随本地 turn 状态刷新） */
  running: boolean;
  turns: AgentTurn[];
  /** 详情是否已从服务端回放；false 表示只有列表摘要壳 */
  loaded: boolean;
}

interface AgentConversationsValue {
  /** 按最近更新倒序的会话列表（侧栏「最近会话」的数据源） */
  conversations: AgentConversation[];
  get: (id: string) => AgentConversation | undefined;
  /** 打开会话：详情未加载时从服务端回放，running 时用 active_run_id 重挂 SSE */
  open: (id: string) => Promise<void>;
  /** 新建服务端会话并发起首轮运行，成功后返回会话 id（调用方跳转 /runs/[id]） */
  start: (input: string) => Promise<string>;
  /** 在既有会话中追问一轮（历史由服务端从转录重建，只传 session_id） */
  send: (conversationId: string, input: string) => void;
  /** 请求后端停止当前正在生成的轮次。 */
  stop: (conversationId: string) => void;
  /** 重命名会话（改索引元数据，成功后同步本地标题）。 */
  rename: (conversationId: string, title: string) => Promise<void>;
  /** 彻底删除会话（服务端转录与索引一并删除；运行中的会话会被服务端拒绝）。 */
  remove: (conversationId: string) => Promise<void>;
}

const Ctx = createContext<AgentConversationsValue | null>(null);

/* —— 转录 entry → AgentTurn 时间线的回放映射 —— */

/** 提取消息的正文纯文本（字符串或 text 块）。 */
function messageText(message: AgentTranscriptMessage): string {
  if (typeof message.content === "string") return message.content;
  return (message.content ?? [])
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("");
}

/** 提取消息里的思考内容（thinking 块，仅 assistant 消息可能携带）。 */
function messageThinking(message: AgentTranscriptMessage): string {
  if (typeof message.content === "string") return "";
  return (message.content ?? [])
    .filter((part) => part.type === "thinking")
    .map((part) => part.text)
    .join("");
}

/**
 * 把转录 entries 回放成 turn 列表：user 消息开启新一轮；assistant 的思考、
 * 正文、tool_calls 按「thinking → text → 工具」的顺序并入时间线（与流式
 * 时的事件顺序一致）；tool 消息按 tool_call_id 合并进对应调用卡片。
 */
function entriesToTurns(entries: AgentSessionEntry[]): AgentTurn[] {
  const turns: AgentTurn[] = [];
  for (const entry of entries) {
    const message = entry.message;
    if (message.role === "user") {
      turns.push({ id: entry.uuid, input: messageText(message), status: "done", segments: [] });
      continue;
    }
    // system 不入转录；万一出现无归属轮次的孤儿消息也直接跳过
    let turn = turns[turns.length - 1];
    if (!turn) continue;
    if (message.role === "assistant") {
      const thinking = messageThinking(message);
      if (thinking) turn = appendThinking(turn, thinking);
      const text = messageText(message);
      if (text) turn = appendText(turn, text);
      for (const call of message.tool_calls ?? []) {
        const args = call.arguments && Object.keys(call.arguments).length > 0
          ? JSON.stringify(call.arguments)
          : (call.raw_arguments ?? "{}");
        turn = appendTool(turn, {
          id: call.id,
          name: call.name,
          label: `${call.name}(${args})`,
        });
      }
      if (entry.finish_reason === "aborted") turn = { ...turn, stopped: true };
    } else if (message.role === "tool") {
      turn =
        patchTool(
          turn,
          (tool) => tool.id === message.tool_call_id,
          () => ({ output: messageText(message) }),
        ) ?? turn;
    }
    turns[turns.length - 1] = turn;
  }
  return turns;
}

/** 列表摘要 → 未加载的会话壳（turns 留空，打开时再回放详情）。 */
function conversationFromSummary(summary: AgentSessionSummary): AgentConversation {
  return {
    id: summary.id,
    title: summary.title ?? summary.last_prompt ?? "未命名会话",
    updatedAt: Date.parse(summary.updated_at),
    running: summary.running,
    turns: [],
    loaded: false,
  };
}

/** 详情 → 完整会话。running 时最后一轮重置为空并挂上 active_run_id，
 * 由调用方从事件 0 回放（转录里该轮已落盘的局部产出全部丢弃）。 */
function conversationFromDetail(detail: AgentSessionDetail): AgentConversation {
  const summary = detail.session;
  const turns = entriesToTurns(detail.entries);
  const running = summary.running && summary.active_run_id != null;
  if (running) {
    const last = turns[turns.length - 1];
    if (last) {
      turns[turns.length - 1] = {
        ...last,
        runId: summary.active_run_id!,
        status: "running",
        segments: [],
        result: undefined,
        error: undefined,
        stopped: undefined,
      };
    }
  }
  return {
    id: summary.id,
    title: summary.title ?? summary.last_prompt ?? "未命名会话",
    updatedAt: Date.parse(summary.updated_at),
    running,
    turns,
    loaded: true,
  };
}

/* —— segments 时间线的不可变更新工具 ——
 * 归约规则：思考/工具事件并入末尾的 process 块（没有则新开一个）；
 * 正文增量并入末尾的 text 段（没有则新开一个）。由此天然形成
 * 「处理过程块 ↔ 正文」交替的时间线，与 loop 的实际执行顺序一致。 */

/** 思维链增量：并入当前 process 块末尾的思考条目，或新开条目/块。 */
function appendThinking(turn: AgentTurn, delta: string): AgentTurn {
  const segments = [...turn.segments];
  const last = segments[segments.length - 1];
  if (last?.kind === "process") {
    const items = [...last.items];
    const tail = items[items.length - 1];
    if (tail?.kind === "thinking") {
      items[items.length - 1] = { ...tail, text: tail.text + delta };
    } else {
      items.push({ kind: "thinking", text: delta });
    }
    segments[segments.length - 1] = { ...last, items };
  } else {
    segments.push({ kind: "process", items: [{ kind: "thinking", text: delta }] });
  }
  return { ...turn, segments };
}

/** 正文增量：并入末尾 text 段，或新开一段（上一段是 process 块时）。 */
function appendText(turn: AgentTurn, delta: string): AgentTurn {
  const segments = [...turn.segments];
  const last = segments[segments.length - 1];
  if (last?.kind === "text") {
    segments[segments.length - 1] = { ...last, text: last.text + delta };
  } else {
    segments.push({ kind: "text", text: delta });
  }
  return { ...turn, segments };
}

/** 追加一个工具条目到当前 process 块（没有则新开一块）。 */
function appendTool(turn: AgentTurn, tool: AgentTurnToolCall): AgentTurn {
  const segments = [...turn.segments];
  const last = segments[segments.length - 1];
  const item: AgentProcessItem = { kind: "tool", ...tool };
  if (last?.kind === "process") {
    segments[segments.length - 1] = { ...last, items: [...last.items, item] };
  } else {
    segments.push({ kind: "process", items: [item] });
  }
  return { ...turn, segments };
}

/** 从后往前找到首个匹配的工具条目并打补丁；找不到返回 null。 */
function patchTool(
  turn: AgentTurn,
  match: (tool: AgentTurnToolCall) => boolean,
  patch: (tool: AgentTurnToolCall) => Partial<AgentTurnToolCall>,
): AgentTurn | null {
  for (let s = turn.segments.length - 1; s >= 0; s -= 1) {
    const segment = turn.segments[s];
    if (segment.kind !== "process") continue;
    for (let i = segment.items.length - 1; i >= 0; i -= 1) {
      const item = segment.items[i];
      if (item.kind !== "tool" || !match(item)) continue;
      const segments = [...turn.segments];
      const items = [...segment.items];
      items[i] = { ...item, ...patch(item) };
      segments[s] = { ...segment, items };
      return { ...turn, segments };
    }
  }
  return null;
}

function applyAgentEvent(turn: AgentTurn, event: AgentEvent): AgentTurn {
  switch (event.type) {
    case "agent_start":
      return { ...turn, provider: event.provider, model: event.model };
    case "thinking_delta":
      return event.delta ? appendThinking(turn, event.delta) : turn;
    case "text_delta":
      return event.delta ? appendText(turn, event.delta) : turn;
    case "tool_call_start":
      // 名称一确定就落一个条目，用户立刻能看到「正在调用哪个工具」
      return event.tool_call
        ? appendTool(turn, {
            id: event.tool_call.id,
            name: event.tool_call.name,
            label: `${event.tool_call.name}(`,
            argsDone: false,
          })
        : turn;
    case "tool_call_delta": {
      const delta = event.delta;
      if (!delta) return turn;
      // 按 id 归属增量；个别端点分片不带 id 时，退化为最后一个参数未完成的调用
      return (
        patchTool(
          turn,
          (tool) => tool.id === event.tool_call_id,
          (tool) => ({ label: tool.label + delta }),
        ) ??
        patchTool(
          turn,
          (tool) => tool.argsDone === false,
          (tool) => ({ label: tool.label + delta }),
        ) ??
        turn
      );
    }
    case "tool_call": {
      const call = event.tool_call;
      if (!call) return turn;
      // 参数定稿：用解析后的完整参数重写 label，替换流式期间的半成品
      const label = `${call.name}(${JSON.stringify(call.arguments)})`;
      return (
        patchTool(
          turn,
          (tool) => tool.id === call.id,
          () => ({ label, argsDone: true }),
        ) ?? appendTool(turn, { id: call.id, name: call.name, label, argsDone: true })
      );
    }
    case "tool_result": {
      const result = event.tool_result;
      if (!result) return turn;
      return (
        patchTool(
          turn,
          (tool) => tool.id === result.tool_call_id,
          () => ({
            output: result.output,
            isError: result.is_error,
            elapsedMs: result.elapsed_ms,
          }),
        ) ?? turn
      );
    }
    case "agent_done":
      return { ...turn, status: "done", result: event.result };
    case "agent_error":
      return { ...turn, status: "error", error: event.error ?? "运行失败，原因未知" };
    case "agent_cancelled":
      return { ...turn, status: "done", stopped: true };
    default:
      return turn;
  }
}

export function AgentConversationsProvider({ children }: { children: React.ReactNode }) {
  const [conversations, setConversations] = useState<AgentConversation[]>([]);
  // runId → 当前 SSE 读取器；仅用于页面卸载时关闭连接，不负责取消后台运行。
  const controllers = useRef(new Map<string, AbortController>());
  // 会话 id → 进行中的详情加载；并发 open 同一会话时复用同一个请求
  const pendingLoads = useRef(new Map<string, Promise<void>>());
  const conversationsRef = useRef(conversations);
  conversationsRef.current = conversations;

  // 挂载时拉取服务端会话列表；本地已有的会话（正在流式）以本地为准。
  useEffect(() => {
    let cancelled = false;
    void listAgentSessions()
      .then((items) => {
        if (cancelled) return;
        setConversations((previous) => {
          const local = new Map(previous.map((c) => [c.id, c]));
          const merged = items.map((item) => {
            const existing = local.get(item.id);
            if (existing) {
              local.delete(item.id);
              return existing;
            }
            return conversationFromSummary(item);
          });
          // 本地刚创建、尚未进入列表接口结果的会话保留在最前
          return [...local.values(), ...merged];
        });
      })
      .catch((error) => {
        console.warn("加载 Agent 会话列表失败", error);
      });
    const activeControllers = controllers.current;
    return () => {
      cancelled = true;
      for (const controller of activeControllers.values()) controller.abort();
      activeControllers.clear();
    };
  }, []);

  /** 对某会话中某轮做不可变更新，并刷新 updatedAt 与派生的 running。 */
  const updateTurn = useCallback(
    (conversationId: string, turnId: string, patch: (turn: AgentTurn) => AgentTurn) => {
      setConversations((previous) =>
        previous.map((conversation) => {
          if (conversation.id !== conversationId) return conversation;
          const turns = conversation.turns.map((turn) =>
            turn.id === turnId ? patch(turn) : turn,
          );
          return {
            ...conversation,
            updatedAt: Date.now(),
            running: turns.some((turn) => turn.status === "running"),
            turns,
          };
        }),
      );
    },
    [],
  );

  /** 连接一个已存在的后台运行；HTTP 错误才会结束，网络抖动由 API 层续传。 */
  const connectRun = useCallback(
    (conversationId: string, turnId: string, runId: string) => {
      controllers.current.get(runId)?.abort();
      const controller = new AbortController();
      controllers.current.set(runId, controller);

      void streamAgentRun(
        runId,
        (event) => {
          updateTurn(conversationId, turnId, (turn) => applyAgentEvent(turn, event));
        },
        { signal: controller.signal },
      )
        .catch((error) => {
          if ((error as Error).name === "AbortError") return;
          const message =
            error instanceof HttpError && error.status === 404
              ? "运行记录不存在或已过期，可能是服务已重启，请重新发起任务"
              : (error as Error).message;
          updateTurn(conversationId, turnId, (turn) => ({
            ...turn,
            status: "error",
            error: message,
          }));
        })
        .finally(() => {
          if (controllers.current.get(runId) === controller) {
            controllers.current.delete(runId);
          }
        });
    },
    [updateTurn],
  );

  /** 打开会话：已加载则直接返回；否则拉详情回放，running 时重挂 SSE。
   * 加载失败会向调用方抛出（并允许重试），不落任何本地状态。 */
  const open = useCallback(
    (id: string) => {
      const existing = conversationsRef.current.find((c) => c.id === id);
      if (existing?.loaded) return Promise.resolve();
      const pending = pendingLoads.current.get(id);
      if (pending) return pending;

      const promise = getAgentSession(id)
        .then((detail) => {
          const conversation = conversationFromDetail(detail);
          setConversations((previous) => {
            const rest = previous.filter((c) => c.id !== id);
            return [conversation, ...rest];
          });
          if (conversation.running) {
            const turn = conversation.turns[conversation.turns.length - 1];
            if (turn?.runId) connectRun(id, turn.id, turn.runId);
          }
        })
        .finally(() => {
          pendingLoads.current.delete(id);
        });
      pendingLoads.current.set(id, promise);
      return promise;
    },
    [connectRun],
  );

  /** 在会话上发起一轮运行：先落本地 running turn，再取得 runId 并连接 SSE。 */
  const runTurn = useCallback(
    (conversationId: string, turnId: string, input: string) => {
      void startAgentRun(input, conversationId)
        .then(({ runId }) => {
          updateTurn(conversationId, turnId, (current) => ({ ...current, runId }));
          connectRun(conversationId, turnId, runId);
        })
        .catch((error) => {
          updateTurn(conversationId, turnId, (current) => ({
            ...current,
            status: "error",
            error: (error as Error).message,
          }));
        });
    },
    [connectRun, updateTurn],
  );

  const start = useCallback(
    async (input: string) => {
      // 新建必须等服务端分配 session_id 才能得到路由地址，因此这一步是
      // 同步等待的；创建失败直接抛给调用方（如尚未配置模型供应商）。
      const { runId, sessionId } = await startAgentRun(input);
      const turnId = crypto.randomUUID();
      setConversations((previous) => [
        {
          id: sessionId,
          // 标题取首轮输入的前 30 字（服务端索引同款朴素策略）
          title: input.slice(0, 30),
          updatedAt: Date.now(),
          running: true,
          turns: [{ id: turnId, runId, input, status: "running", segments: [] }],
          loaded: true,
        },
        ...previous,
      ]);
      connectRun(sessionId, turnId, runId);
      return sessionId;
    },
    [connectRun],
  );

  const send = useCallback(
    (conversationId: string, input: string) => {
      const turnId = crypto.randomUUID();
      setConversations((previous) =>
        previous.map((conversation) =>
          conversation.id === conversationId
            ? {
                ...conversation,
                updatedAt: Date.now(),
                running: true,
                turns: [
                  ...conversation.turns,
                  { id: turnId, input, status: "running", segments: [] },
                ],
              }
            : conversation,
        ),
      );
      runTurn(conversationId, turnId, input);
    },
    [runTurn],
  );

  const stop = useCallback((conversationId: string) => {
    const conversation = conversationsRef.current.find((item) => item.id === conversationId);
    const turn = conversation?.turns.find((item) => item.status === "running");
    if (!turn?.runId) {
      console.warn("Agent 尚未取得运行编号，暂时无法发送停止请求");
      return;
    }
    void cancelAgentRun(turn.runId).catch((error) => {
      // 保持 SSE 连接和 running 状态；停止失败时 Agent 可能仍在执行，不能在
      // 客户端伪造终态。后续真实终态仍会通过事件流正常落到界面。
      console.warn("停止 Agent 运行失败，请稍后重试", error);
    });
  }, []);

  const rename = useCallback(async (conversationId: string, title: string) => {
    await renameAgentSession(conversationId, title);
    setConversations((previous) =>
      previous.map((conversation) =>
        conversation.id === conversationId ? { ...conversation, title } : conversation,
      ),
    );
  }, []);

  const remove = useCallback(async (conversationId: string) => {
    // 服务端会拒绝删除运行中的会话（400），错误交给调用方提示
    await deleteAgentSession(conversationId);
    pendingLoads.current.delete(conversationId);
    setConversations((previous) =>
      previous.filter((conversation) => conversation.id !== conversationId),
    );
  }, []);

  const value = useMemo<AgentConversationsValue>(
    () => ({
      conversations: [...conversations].sort((a, b) => b.updatedAt - a.updatedAt),
      get: (id) => conversations.find((conversation) => conversation.id === id),
      open,
      start,
      send,
      stop,
      rename,
      remove,
    }),
    [conversations, open, start, send, stop, rename, remove],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAgentConversations(): AgentConversationsValue {
  const context = useContext(Ctx);
  if (!context) throw new Error("useAgentConversations 必须在 AgentConversationsProvider 内使用");
  return context;
}
