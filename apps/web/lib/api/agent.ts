import { resolveRequestUrl, redirectToLoginOn401, HttpError, request } from "@/lib/http";

/** Agent 执行事件（见 movieclaw_agent.events.AgentEvent）。 */
export type AgentEventType =
  | "agent_start"
  | "thinking_delta"
  | "text_delta"
  | "tool_call_start"
  | "tool_call_delta"
  | "tool_call"
  | "tool_result"
  | "agent_done"
  | "agent_error"
  | "agent_cancelled";

export interface AgentToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

/** 工具执行回执（tool_result 事件的载荷）。 */
export interface AgentToolResult {
  tool_call_id: string;
  name: string;
  /** 喂回模型的结果文本（事件里截断到 2000 字） */
  output: string;
  is_error: boolean;
  elapsed_ms: number;
}

/** agent_done 的终态载荷（text/thinking 为最后一步产出，usage 为全程累计）。 */
export interface AgentDone {
  text: string | null;
  thinking: string | null;
  finish_reason: string | null;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  /** 模型调用步数（agent loop 的轮数） */
  steps: number;
  model: string;
  provider: string;
  elapsed_ms: number;
}

export interface AgentEvent {
  type: AgentEventType;
  run_id: string;
  /** thinking_delta / text_delta / tool_call_delta 的增量文本 */
  delta?: string;
  /** tool_call_start：仅含 id/name；tool_call：参数完整的调用 */
  tool_call?: AgentToolCall;
  /** tool_call_delta：增量所属的工具调用 id */
  tool_call_id?: string;
  tool_result?: AgentToolResult;
  /** agent_start：实际路由到的供应商与模型 */
  provider?: string;
  model?: string;
  result?: AgentDone;
  error?: string;
}

/* —— 服务端会话（JSONL 转录的投影，见 movieclaw_api.schemas.agent）—— */

/** 转录消息的内容块（movieclaw_llm ContentPart 的前端投影）。 */
export type AgentContentPart =
  | { type: "text"; text: string }
  | { type: "thinking"; text: string }
  | { type: "image"; url?: string | null; data?: string | null; media_type?: string | null };

/** 转录里的一次工具调用（参数已由协议层解析为对象）。 */
export interface AgentTranscriptToolCall {
  id: string;
  name: string;
  arguments?: Record<string, unknown>;
  /** 供应商返回的原始参数 JSON；arguments 解析失败时用它兜底展示 */
  raw_arguments?: string;
}

/** 转录里的 LLM API 原样消息（按 role 分发渲染）。 */
export interface AgentTranscriptMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | AgentContentPart[];
  /** 仅 assistant */
  tool_calls?: AgentTranscriptToolCall[] | null;
  /** 仅 tool：结果所属的调用 id（合并进对应调用卡片） */
  tool_call_id?: string | null;
  name?: string | null;
}

/** 会话详情里的一条消息 entry（信封 + API 格式消息）。 */
export interface AgentSessionEntry {
  uuid: string;
  timestamp: string;
  message: AgentTranscriptMessage;
  /** 以下仅 assistant 消息携带（运行元数据） */
  model?: string | null;
  usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number } | null;
  /** 约定含 "aborted"：该步产出时运行被取消 */
  finish_reason?: string | null;
}

/** 会话列表项（running 由 active_run_id + 心跳窗派生）。 */
export interface AgentSessionSummary {
  id: string;
  title: string | null;
  last_prompt: string | null;
  entry_count: number;
  running: boolean;
  /** running 为 true 时可用它重新挂上 SSE 事件流 */
  active_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentSessionDetail {
  session: AgentSessionSummary;
  entries: AgentSessionEntry[];
}

interface ApiEnvelope<T> {
  success: boolean;
  code: string;
  message: string;
  data: T;
}

/**
 * 创建后台 Agent 运行；响应只确认已入队，不等待模型开始输出。
 * 传 sessionId 表示在既有服务端会话上续聊（历史由服务端从转录重建）；
 * 留空则新建会话，返回的 session_id 供后续续聊时带回。
 */
export async function startAgentRun(
  input: string,
  sessionId?: string,
): Promise<{ runId: string; sessionId: string }> {
  const response = await request<ApiEnvelope<{ run_id: string; session_id: string }>>(
    "/agent/start",
    {
      method: "POST",
      body: JSON.stringify(sessionId ? { input, session_id: sessionId } : { input }),
    },
  );
  return { runId: response.data.run_id, sessionId: response.data.session_id };
}

/** 最近会话列表（按最后活跃时间倒序）。 */
export async function listAgentSessions(): Promise<AgentSessionSummary[]> {
  const response = await request<ApiEnvelope<AgentSessionSummary[]>>("/agent/sessions");
  return response.data;
}

/** 会话详情（完整消息 entry 回放）。 */
export async function getAgentSession(sessionId: string): Promise<AgentSessionDetail> {
  const response = await request<ApiEnvelope<AgentSessionDetail>>(
    `/agent/sessions/${sessionId}`,
  );
  return response.data;
}

/** 重命名会话（标题只改索引元数据，转录内容不变）。 */
export async function renameAgentSession(
  sessionId: string,
  title: string,
): Promise<void> {
  await request<ApiEnvelope<AgentSessionSummary>>(`/agent/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

/** 删除会话（转录文件与索引一并删除；运行中的会话会被服务端拒绝）。 */
export async function deleteAgentSession(sessionId: string): Promise<void> {
  await request<ApiEnvelope<Record<string, never>>>(`/agent/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

/** 幂等请求停止后台运行；真正的终态由 stream 中的 agent_cancelled 确认。 */
export async function cancelAgentRun(runId: string): Promise<void> {
  await request<ApiEnvelope<Record<string, never>>>(`/agent/runs/${runId}/cancel`, {
    method: "POST",
  });
}

function isTerminal(event: AgentEvent): boolean {
  return (
    event.type === "agent_done" ||
    event.type === "agent_error" ||
    event.type === "agent_cancelled"
  );
}

async function responseError(response: Response): Promise<HttpError> {
  let message = `Request failed with status ${response.status}`;
  let details: unknown = null;
  try {
    details = await response.json();
    if (details && typeof details === "object" && "message" in details) {
      message = String((details as { message: unknown }).message);
    }
  } catch {
    // 非 JSON 错误体，保留默认 message
  }
  redirectToLoginOn401(response.status);
  return new HttpError(message, response.status, details);
}

/** 可被 AbortSignal 打断的重连等待，避免页面卸载后残留定时器。 */
function waitBeforeRetry(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/**
 * 订阅一次后台运行的 SSE 事件。
 *
 * 每个数据帧必须带递增 ``id``。连接意外结束时用 Last-Event-ID 续传，服务端
 * 因此只回放缺失部分；HTTP 错误（含运行过期 404）直接交给调用方，只有网络
 * 中断才指数退避重试。函数收到终态事件后返回，不会再次连接已完成的运行。
 */
export async function streamAgentRun(
  runId: string,
  onEvent: (event: AgentEvent) => void,
  opts?: { signal?: AbortSignal; afterEventId?: number },
): Promise<void> {
  let lastEventId = opts?.afterEventId ?? 0;
  let retryDelay = 500;

  for (;;) {
    let response: Response;
    try {
      const headers = new Headers({ Accept: "text/event-stream" });
      if (lastEventId > 0) headers.set("Last-Event-ID", String(lastEventId));
      response = await fetch(resolveRequestUrl(`/agent/runs/${runId}/stream`), {
        headers,
        signal: opts?.signal,
      });
    } catch (error) {
      if ((error as Error).name === "AbortError") throw error;
      await waitBeforeRetry(retryDelay, opts?.signal);
      retryDelay = Math.min(retryDelay * 2, 5000);
      continue;
    }

    if (!response.ok) throw await responseError(response);
    if (!response.body) {
      throw new HttpError("当前环境不支持流式响应", response.status, null);
    }

    retryDelay = 500;
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let terminal = false;

    const dispatch = (block: string) => {
      let id = 0;
      let eventName = "";
      let data = "";
      for (const line of block.split("\n")) {
        if (line.startsWith("id: ")) id = Number(line.slice(4).trim());
        else if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      // SSE 注释心跳没有 id/event/data，直接忽略。
      if (!id || !eventName || !data || id <= lastEventId) return;
      const event = JSON.parse(data) as AgentEvent;
      onEvent(event);
      lastEventId = id;
      terminal = isTerminal(event);
    };

    let streamError: unknown = null;
    try {
      while (!terminal) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let separator: number;
        while ((separator = buffer.indexOf("\n\n")) !== -1) {
          dispatch(buffer.slice(0, separator));
          buffer = buffer.slice(separator + 2);
          if (terminal) break;
        }
      }
    } catch (error) {
      streamError = error;
    } finally {
      reader.releaseLock();
    }

    if (terminal) return;
    if ((streamError as Error | null)?.name === "AbortError") throw streamError;
    // 包括 reader.read() 在传输中途抛出的网络错误：保留 lastEventId，按同一
    // 退避策略重新 GET，服务端只补发尚未确认的事件。
    await waitBeforeRetry(retryDelay, opts?.signal);
    retryDelay = Math.min(retryDelay * 2, 5000);
  }
}
