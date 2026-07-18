import { request } from "@/lib/http";
import type { ConfiguredSite, SiteAuthType } from "@/lib/api/extension";

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
// 目录（可选项）：系统支持的可配置站点及其授权要求
// ---------------------------------------------------------------------------

/** 某授权类型及其要求用户填写的字段（见 schemas.site.AuthTypeRequirement）。 */
export interface AuthTypeRequirement {
  auth_type: SiteAuthType;
  required_fields: string[];
}

/** 目录项：一个系统支持的可配置站点（见 schemas.site.CatalogItem）。 */
export interface CatalogItem {
  site_id: string;
  display_name: string;
  base_url: string;
  supported_auth_types: AuthTypeRequirement[];
}

/** 列出系统支持的可配置站点（供前端渲染"可添加"列表与表单）。 */
export function listSiteCatalog(init?: RequestInit): Promise<CatalogItem[]> {
  return unwrap(request<ApiEnvelope<CatalogItem[]>>("/sites/catalog", init));
}

// ---------------------------------------------------------------------------
// 已配置站点：CRUD + 验证
// ---------------------------------------------------------------------------

/** 列出所有已配置站点及其验证状态。 */
export function listConfiguredSites(init?: RequestInit): Promise<ConfiguredSite[]> {
  return unwrap(request<ApiEnvelope<ConfiguredSite[]>>("/sites", init));
}

/** 站点种子缓存与同步节奏统计（见 schemas.site.SiteSyncStatsView）。 */
export interface SiteSyncStats {
  torrent_count: number;
  tracking_since: string | null;
  /** 上次同步完成时间；null = 从未同步过 */
  last_sync_at: string | null;
  /** 上次同步成功时间；null = 从未成功（站点故障期间 last_sync_at 仍推进，此值停留） */
  last_success_at: string | null;
  /** 下次同步到期时刻；null = 立即到期（新站等待首刷） */
  next_sync_at: string | null;
  sync_interval_seconds: number | null;
  last_new_count: number | null;
  /** 上次同步失败原因；null = 上次同步成功 */
  last_error: string | null;
  /** 连续同步失败次数；成功清零 */
  consecutive_failures: number;
}

/** 按 site_id 返回各站点的本地缓存统计；从未同步过的站点没有条目。 */
export function listSiteSyncStats(init?: RequestInit): Promise<Record<string, SiteSyncStats>> {
  return unwrap(request<ApiEnvelope<Record<string, SiteSyncStats>>>("/sites/sync-stats", init));
}

/** 获取单个已配置站点详情（用于轮询验证进度）。 */
export function getConfiguredSite(siteId: string, init?: RequestInit): Promise<ConfiguredSite> {
  return unwrap(request<ApiEnvelope<ConfiguredSite>>(`/sites/${siteId}`, init));
}

/** 配置站点时提交的授权信息。按 auth_type 只需填对应字段。 */
export interface SiteConfigPayload {
  auth_type: SiteAuthType;
  cookie?: string | null;
  api_key?: string | null;
  username?: string | null;
  password?: string | null;
  enabled?: boolean;
}

/** 新增配置一个站点（保存后后端异步验证）。 */
export function configureSite(
  siteId: string,
  payload: SiteConfigPayload,
): Promise<ConfiguredSite> {
  return unwrap(
    request<ApiEnvelope<ConfiguredSite>>("/sites", {
      method: "POST",
      body: JSON.stringify({ site_id: siteId, ...payload }),
    }),
  );
}

/** 更新已配置站点的授权信息（更新后后端重新异步验证）。 */
export function updateSite(siteId: string, payload: SiteConfigPayload): Promise<ConfiguredSite> {
  return unwrap(
    request<ApiEnvelope<ConfiguredSite>>(`/sites/${siteId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  );
}

/** 启用 / 停用站点。 */
export function setSiteEnabled(siteId: string, enabled: boolean): Promise<ConfiguredSite> {
  return unwrap(
    request<ApiEnvelope<ConfiguredSite>>(`/sites/${siteId}/status`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    }),
  );
}

/** 手动重新触发一次验证。 */
export function reverifySite(siteId: string): Promise<ConfiguredSite> {
  return unwrap(
    request<ApiEnvelope<ConfiguredSite>>(`/sites/${siteId}/verify`, { method: "POST" }),
  );
}

/** 删除站点配置（连带清理 cookie 缓存）。 */
export function deleteSite(siteId: string): Promise<{ site_id: string }> {
  return unwrap(
    request<ApiEnvelope<{ site_id: string }>>(`/sites/${siteId}`, { method: "DELETE" }),
  );
}
