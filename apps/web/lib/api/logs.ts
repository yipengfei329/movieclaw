import { request } from "@/lib/http";

/** 后端统一响应信封（见 movieclaw_api.schemas.response.ApiResponse） */
interface ApiEnvelope<T> {
  success: boolean;
  code: string;
  message: string;
  data: T;
}

async function unwrap<T>(promise: Promise<ApiEnvelope<T>>): Promise<T> {
  return (await promise).data;
}

// ---------------------------------------------------------------------------
// 系统日志：后端按天落盘的运行日志（见 movieclaw_api.api.routes.logs）
// ---------------------------------------------------------------------------

/** 一个可查看的日志日期（对应服务端磁盘上的一个日志文件） */
export interface LogDay {
  day: string;
  size_bytes: number;
}

/** 某天的日志内容；truncated 为 true 表示只返回了末尾片段 */
export interface LogContent {
  day: string;
  lines: string[];
  total_lines: number;
  truncated: boolean;
  size_bytes: number;
}

/** 列出全部可查看的日志日期（日期倒序，最新在前） */
export async function fetchLogDays(): Promise<LogDay[]> {
  const data = await unwrap(
    request<ApiEnvelope<{ days: LogDay[] }>>("/system/logs"),
  );
  return data.days;
}

/** 读取某天的日志。tail 为只取末尾多少行，0 表示全天完整内容。 */
export async function fetchLogContent(day: string, tail?: number): Promise<LogContent> {
  const query = tail === undefined ? "" : `?tail=${tail}`;
  return unwrap(request<ApiEnvelope<LogContent>>(`/system/logs/${day}${query}`));
}
