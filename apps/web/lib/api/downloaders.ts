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

/** 已适配的下载器类型（与后端 movieclaw_db ClientType 对应）。 */
export type DownloaderClientType = "qbittorrent" | "transmission";

/** 连接验证状态（与站点配置共用同一状态机语义）。 */
export type DownloaderStatus = "pending" | "verifying" | "active" | "failed";

/** 已配置下载器的对外视图（见 schemas.downloader.DownloaderView，脱敏无密码）。 */
export interface ConfiguredDownloader {
  id: number;
  name: string;
  client_type: DownloaderClientType;
  url: string;
  username: string | null;
  save_path: string | null;
  enabled: boolean;
  /** 是否为默认下载器（一键下载不选目标时投给它） */
  is_default: boolean;
  status: DownloaderStatus;
  /** 是否可用 = 已启用且连接测试通过 */
  usable: boolean;
  /** 最近一次连接成功获取的版本号，如 "v5.0.2" */
  version: string | null;
  last_error: string | null;
  last_checked_at: string | null;
  created_at: string;
  updated_at: string;
}

/** 新增/更新下载器的请求体（见 schemas.downloader.DownloaderPayload）。 */
export interface DownloaderPayload {
  name: string;
  client_type: DownloaderClientType;
  url: string;
  username?: string | null;
  password?: string | null;
  save_path?: string | null;
  enabled?: boolean;
}

/** 列出所有已配置的下载器及连接状态。 */
export function listDownloaders(init?: RequestInit): Promise<ConfiguredDownloader[]> {
  return unwrap(request<ApiEnvelope<ConfiguredDownloader[]>>("/downloaders", init));
}

/** 获取单个下载器详情（用于轮询连接测试进度）。 */
export function getDownloader(id: number, init?: RequestInit): Promise<ConfiguredDownloader> {
  return unwrap(request<ApiEnvelope<ConfiguredDownloader>>(`/downloaders/${id}`, init));
}

/** 添加一个下载器（保存后后端异步测试连接）。 */
export function createDownloader(payload: DownloaderPayload): Promise<ConfiguredDownloader> {
  return unwrap(
    request<ApiEnvelope<ConfiguredDownloader>>("/downloaders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

/** 更新下载器配置（更新后后端重新测试连接）。 */
export function updateDownloader(
  id: number,
  payload: DownloaderPayload,
): Promise<ConfiguredDownloader> {
  return unwrap(
    request<ApiEnvelope<ConfiguredDownloader>>(`/downloaders/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  );
}

/** 启用 / 停用下载器。 */
export function setDownloaderEnabled(
  id: number,
  enabled: boolean,
): Promise<ConfiguredDownloader> {
  return unwrap(
    request<ApiEnvelope<ConfiguredDownloader>>(`/downloaders/${id}/status`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  );
}

/** 设为默认下载器。注意：其他条目的 is_default 会随之变化，调用后应整体刷新列表。 */
export function setDefaultDownloader(id: number): Promise<ConfiguredDownloader> {
  return unwrap(
    request<ApiEnvelope<ConfiguredDownloader>>(`/downloaders/${id}/default`, { method: "POST" }),
  );
}

/** 手动重新测试一次连接。 */
export function reverifyDownloader(id: number): Promise<ConfiguredDownloader> {
  return unwrap(
    request<ApiEnvelope<ConfiguredDownloader>>(`/downloaders/${id}/verify`, { method: "POST" }),
  );
}

/** 删除下载器配置。 */
export function deleteDownloader(id: number): Promise<Record<string, never>> {
  return unwrap(
    request<ApiEnvelope<Record<string, never>>>(`/downloaders/${id}`, { method: "DELETE" }),
  );
}

/** 手动提交下载的请求体（见 schemas.downloader.DownloadSubmitPayload）。 */
export interface DownloadSubmitPayload {
  /** 种子所属站点 ID（TorrentHit.site_id） */
  site_id: string;
  /** 种子下载入口（TorrentHit.download_url） */
  download_url: string;
  /** 入库目标库（可选）：带上后保存目录由库推导（主根/标题 (年份)） */
  library_id?: number | null;
  /** 条目标题（推导条目子目录用；身份未确认时不要带） */
  title?: string | null;
  year?: number | null;
  /** 种子副标题（识别线索：中文片名/「全N集」帮扫描器收敛拼音命名种子） */
  subtitle?: string | null;
}

/** 手动提交下载的结果（见 schemas.downloader.DownloadSubmitView）。 */
export interface DownloadSubmitResult {
  info_hash: string | null;
  name: string;
  /** 种子提交前已存在于下载器（幂等，未重复添加） */
  already_exists: boolean;
  downloader_id: number;
  downloader_name: string;
  /** 实际使用的保存目录（null = 下载器自身默认目录） */
  save_path: string | null;
}

/**
 * 把一条搜索结果种子提交到默认下载器：后端带站点登录态取回 .torrent 再递交，
 * 保存目录用默认下载器配置的默认目录。失败抛 HttpError，message 为可读中文。
 */
export function submitTorrentDownload(
  payload: DownloadSubmitPayload,
): Promise<DownloadSubmitResult> {
  return unwrap(
    request<ApiEnvelope<DownloadSubmitResult>>("/downloaders/submit", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}
