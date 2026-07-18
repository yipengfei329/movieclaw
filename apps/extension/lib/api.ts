/**
 * MovieClaw 后端 API 客户端（插件侧）。
 *
 * 说明：
 * - 所有插件侧接口都需带 `Authorization: Bearer <token>`。
 * - 跨域访问后端需要该源的 host 权限；插件用 optional_host_permissions 按需申请，
 *   拿到后 MV3 的 fetch 天然绕过 CORS，无需后端配置 CORS。
 */

import type { BackendConfig } from './config';

/** 由后端地址推导其 host 权限匹配模式，如 http://localhost:8000 → http://localhost/* */
export function backendOriginPattern(backendUrl: string): string {
  const u = new URL(backendUrl);
  return `${u.protocol}//${u.hostname}/*`;
}

/** 确保已获得后端源的 host 权限；没有则发起申请（需在用户手势中调用）。 */
export async function ensureBackendPermission(backendUrl: string): Promise<boolean> {
  const origins = [backendOriginPattern(backendUrl)];
  if (await chrome.permissions.contains({ origins })) return true;
  return chrome.permissions.request({ origins });
}

/** 统一请求：拼 URL、带令牌、解包 ApiResponse、错误转为可读异常。 */
async function request<T>(
  config: BackendConfig,
  path: string,
  init?: RequestInit,
): Promise<T> {
  const url = new URL(path, config.backendUrl).toString();
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${config.syncToken}`,
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    // 网络层失败（地址不可达、未授权该源、离线等）
    throw new Error('无法连接后端，请检查地址与服务');
  }

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    // 后端统一错误信封：{ success:false, code, message }
    const message = body?.message || `请求失败（HTTP ${res.status}）`;
    throw new Error(message);
  }
  return body.data as T;
}

/** 连接自检返回体 */
export interface PingResult {
  ok: boolean;
  app_name: string;
}

type SiteStatus = 'pending' | 'verifying' | 'active' | 'failed';

/** 推送 Cookie 的返回体 */
export interface CookieSyncResult {
  site_id: string;
  display_name: string;
  domain: string;
  status: SiteStatus;
  usable: boolean;
}

/** 支持 Cookie 同步的站点视图（后端已过滤掉仅支持 API-Key 的站点） */
export interface ExtensionSiteView {
  site_id: string;
  display_name: string;
  /** 该站点的匹配域名（可注册域名），用于比对当前标签页 */
  domain: string;
  configured: boolean;
  status: SiteStatus | null;
  usable: boolean;
}

export const api = {
  /** 连接与令牌自检 */
  ping: (config: BackendConfig) =>
    request<PingResult>(config, '/api/v1/extension/ping'),

  /** 列出所有支持 Cookie 同步的站点（含匹配域名与配置状态） */
  listSites: (config: BackendConfig) =>
    request<ExtensionSiteView[]>(config, '/api/v1/extension/sites'),

  /** 推送某域名的 Cookie，返回命中的站点与验证状态 */
  pushCookies: (config: BackendConfig, domain: string, cookie: string) =>
    request<CookieSyncResult>(config, '/api/v1/extension/cookies', {
      method: 'POST',
      body: JSON.stringify({ domain, cookie }),
    }),
};
