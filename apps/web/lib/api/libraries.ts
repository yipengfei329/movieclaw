import { request } from "@/lib/http";
import type { MediaType } from "@/lib/media-types";

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

/**
 * 媒体库（见 schemas.library.LibraryView）：
 * "我拥有哪些影视内容、放在哪里"的权威定义。订阅/手动下载按
 * "入库到哪个库"确定保存路径（主根/标题 (年份)）。
 */
export interface LibraryStats {
  /** 已识别的媒体条目数 */
  item_count: number;
  /** 在账文件总数（含待识别） */
  file_count: number;
  total_size_bytes: number;
  /** 待识别文件数 */
  unidentified_count: number;
  /** 标记 missing 的文件数（缺失清单入口） */
  missing_count: number;
}

export interface MediaLibrary {
  id: number;
  name: string;
  /** 每库单一类型（movie / tv），创建后不可改 */
  kind: MediaType;
  /** 根路径列表（绝对路径），第一个为主根——新入库落在这里 */
  root_paths: string[];
  primary_root: string | null;
  /** 是否为该类型的默认库（订阅/手动下载不选库时用它） */
  is_default: boolean;
  /** 库存统计（library_file 台账聚合） */
  stats: LibraryStats;
  /** 是否正在扫描 */
  scanning: boolean;
  /** 扫描实时进度（没在扫为 null）——前端在库封面上画进度环 */
  scan_progress: ScanProgress | null;
  /** 最近一次扫描结论（扫描常毫秒级完成，靠它给"点了有反应"的反馈） */
  last_scan: LastScan | null;
  created_at: string;
  updated_at: string;
}

/** 扫描实时进度。 */
export interface ScanProgress {
  processed: number;
  total: number;
}

/** 最近一次扫描的结论。 */
export interface LastScan {
  finished_at: string;
  scanned: number;
  identified: number;
  unidentified: number;
  marked_missing: number;
  errors: string[];
}

/** 库内一个媒体条目的库存聚合（单库海报墙的一格）。 */
export interface LibraryItem {
  media_item_id: number;
  kind: MediaType;
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_url: string | null;
  file_count: number;
  total_size_bytes: number;
  /** 在库的季号列表（电影为空） */
  seasons: number[];
  /** 去重的 (季,集) 单元数（电影为 0） */
  episode_count: number;
  /** 去重的介质规格标签（如 ["2160p"]），探测不到为空 */
  resolutions: string[];
  /** 标记 missing 的文件数（>0 时提示） */
  missing_count: number;
  /** 最近一次文件入账时间（ISO 字符串），首页「最近添加」排序依据 */
  added_at: string | null;
}

/** 待识别清单的一行。 */
export interface UnidentifiedFile {
  id: number;
  library_id: number;
  library_name: string;
  file_path: string;
  size_bytes: number;
  season_number: number;
  episode_number: number;
}

/** 创建/更新库的请求体。kind 仅创建时生效。 */
export interface LibraryPayload {
  name: string;
  kind: MediaType;
  root_paths: string[];
}

/** 列出全部媒体库（可按类型过滤）。 */
export function listLibraries(kind?: MediaType, init?: RequestInit): Promise<MediaLibrary[]> {
  const qs = kind ? `?kind=${kind}` : "";
  return unwrap(request<ApiEnvelope<MediaLibrary[]>>(`/libraries${qs}`, init));
}

// 默认库的轻量缓存：搜索结果页大量下载按钮共享一次 /libraries 请求，
// 60 秒后过期重取（库配置变更不常见，误差可接受）。
let _libraryCache: { at: number; promise: Promise<MediaLibrary[]> } | null = null;

/** 某类型的默认库（带 60s 缓存）；没有任何库或请求失败返回 null。 */
export async function defaultLibraryFor(kind: MediaType | null | undefined): Promise<MediaLibrary | null> {
  if (!kind) return null;
  const now = Date.now();
  if (!_libraryCache || now - _libraryCache.at > 60_000) {
    _libraryCache = { at: now, promise: listLibraries() };
  }
  try {
    const libs = await _libraryCache.promise;
    return libs.find((l) => l.kind === kind && l.is_default) ?? null;
  } catch {
    _libraryCache = null;
    return null;
  }
}

/** 创建媒体库（该类型首个库自动成为默认）。 */
export function createLibrary(payload: LibraryPayload): Promise<MediaLibrary> {
  return unwrap(
    request<ApiEnvelope<MediaLibrary>>("/libraries", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

/** 更新媒体库（名称与根路径）。 */
export function updateLibrary(id: number, payload: LibraryPayload): Promise<MediaLibrary> {
  return unwrap(
    request<ApiEnvelope<MediaLibrary>>(`/libraries/${id}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  );
}

/** 设为该类型的默认库。同类型其他库的默认标记随之取消，调用后应整体刷新列表。 */
export function setDefaultLibrary(id: number): Promise<MediaLibrary> {
  return unwrap(request<ApiEnvelope<MediaLibrary>>(`/libraries/${id}/default`, { method: "POST" }));
}

/** 删除媒体库（不动磁盘文件；其订阅回落到该类型默认库）。 */
export function deleteLibrary(id: number): Promise<Record<string, never>> {
  return unwrap(request<ApiEnvelope<Record<string, never>>>(`/libraries/${id}`, { method: "DELETE" }));
}

/** 库内媒体条目的库存聚合（单库海报墙数据源）。 */
export function listLibraryItems(id: number, init?: RequestInit): Promise<LibraryItem[]> {
  return unwrap(request<ApiEnvelope<LibraryItem[]>>(`/libraries/${id}/items`, init));
}

/** 触发一次库扫描（后台执行；已在扫描中时后端返回 409）。 */
export function startLibraryScan(id: number): Promise<{ started: boolean; message: string }> {
  return unwrap(
    request<ApiEnvelope<{ started: boolean; message: string }>>(`/libraries/${id}/scan`, {
      method: "POST",
    }),
  );
}

/** 待识别清单（可按库过滤）。 */
export function listUnidentified(libraryId?: number): Promise<UnidentifiedFile[]> {
  const qs = libraryId != null ? `?library_id=${libraryId}` : "";
  return unwrap(request<ApiEnvelope<UnidentifiedFile[]>>(`/libraries/unidentified${qs}`));
}

/** 认领待识别文件：挂到指定 TMDB 条目。 */
export function claimFile(
  fileId: number,
  payload: { tmdb_id: number; season_number?: number; episode_number?: number },
): Promise<Record<string, never>> {
  return unwrap(
    request<ApiEnvelope<Record<string, never>>>(`/libraries/files/${fileId}/claim`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

/** 从台账忽略一个待识别文件（不动磁盘）。 */
export function ignoreFile(fileId: number): Promise<Record<string, never>> {
  return unwrap(
    request<ApiEnvelope<Record<string, never>>>(`/libraries/files/${fileId}`, {
      method: "DELETE",
    }),
  );
}

/** 批量忽略整库的待识别文件（只删台账，不动磁盘）。 */
export function clearUnidentified(libraryId: number): Promise<{ cleared: number }> {
  return unwrap(
    request<ApiEnvelope<{ cleared: number }>>(`/libraries/unidentified/clear`, {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId }),
    }),
  );
}

/** 缺失清单里的一个文件。 */
export interface MissingFile {
  id: number;
  file_path: string;
  season_number: number;
  episode_number: number;
  size_bytes: number;
}

/** 缺失清单的一行：按媒体条目聚合。 */
export interface MissingItem {
  media_item_id: number;
  kind: MediaType;
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_url: string | null;
  /** 该条目已有订阅时给出——清理前提示（订阅可能重新下回来） */
  subscription_id: number | null;
  files: MissingFile[];
}

/** 缺失清单（文件已不在磁盘的库存，按条目聚合）。 */
export function listMissing(libraryId: number): Promise<MissingItem[]> {
  return unwrap(request<ApiEnvelope<MissingItem[]>>(`/libraries/${libraryId}/missing`));
}

/** 清理缺失记录（只删台账，绝不动磁盘）；不传 mediaItemId 清整库。 */
export function clearMissing(
  libraryId: number,
  mediaItemId?: number,
): Promise<{ cleared: number }> {
  return unwrap(
    request<ApiEnvelope<{ cleared: number }>>(`/libraries/missing/clear`, {
      method: "POST",
      body: JSON.stringify({ library_id: libraryId, media_item_id: mediaItemId ?? null }),
    }),
  );
}

/** 重新下载：缺失单元交回订阅管线（无订阅则按缺失季自动创建）。 */
export function redownloadMissing(
  libraryId: number,
  mediaItemId: number,
): Promise<{ subscription_id: number; requeued: number }> {
  return unwrap(
    request<ApiEnvelope<{ subscription_id: number; requeued: number }>>(
      `/libraries/missing/redownload`,
      {
        method: "POST",
        body: JSON.stringify({ library_id: libraryId, media_item_id: mediaItemId }),
      },
    ),
  );
}
