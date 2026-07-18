/**
 * 插件本地配置（chrome.storage.local）：后端连接、全局自动同步开关、已同步站点、同步结果。
 */

export interface BackendConfig {
  /** MovieClaw 后端地址，如 http://localhost:8000 */
  backendUrl: string;
  /** 后端「浏览器插件同步」里生成的令牌 */
  syncToken: string;
  /** 全局开关：已同步过的站点，是否在后台随 Cookie 变化自动保持最新 */
  autoSyncEnabled: boolean;
}

const CONFIG_KEY = 'backendConfig';

/** 读取后端配置；缺省时 autoSyncEnabled 默认开启。 */
export async function loadConfig(): Promise<BackendConfig> {
  const result = await chrome.storage.local.get(CONFIG_KEY);
  const stored = result[CONFIG_KEY] as Partial<BackendConfig> | undefined;
  return {
    backendUrl: stored?.backendUrl ?? '',
    syncToken: stored?.syncToken ?? '',
    autoSyncEnabled: stored?.autoSyncEnabled ?? true,
  };
}

/** 保存后端配置。 */
export async function saveConfig(config: BackendConfig): Promise<void> {
  await chrome.storage.local.set({ [CONFIG_KEY]: config });
}

/** 是否已完成后端配置（地址与令牌都填了）。 */
export function isConfigured(config: BackendConfig): boolean {
  return Boolean(config.backendUrl.trim() && config.syncToken.trim());
}

// ---------------------------------------------------------------------------
// 已同步站点：同步过一次即纳入管理，供后台在全局开关开启时自动保持最新
// ---------------------------------------------------------------------------

/** 一个已同步（被管理）的站点。 */
export interface ManagedSite {
  /** 同步时所在的精确主机名，如 kp.m-team.cc；后台据此读取该 URL 下的 Cookie */
  host: string;
  /** 命中的站点标识 */
  siteId: string;
  /** 站点展示名，供 UI 显示 */
  displayName: string;
}

/** 已同步站点表：键为可注册域名（如 m-team.cc），便于从 cookies.onChanged 事件快速命中。 */
export type ManagedSitesMap = Record<string, ManagedSite>;

const MANAGED_KEY = 'managedSites';

/** 读取已同步站点表。 */
export async function loadManagedSites(): Promise<ManagedSitesMap> {
  const result = await chrome.storage.local.get(MANAGED_KEY);
  return (result[MANAGED_KEY] as ManagedSitesMap) ?? {};
}

/** 登记（同步成功后调用）或移除一个已同步站点。 */
export async function setManagedSite(domain: string, entry: ManagedSite | null): Promise<void> {
  const map = await loadManagedSites();
  if (entry) map[domain] = entry;
  else delete map[domain];
  await chrome.storage.local.set({ [MANAGED_KEY]: map });
}

// ---------------------------------------------------------------------------
// 最近一次同步结果（供弹窗展示"上次同步于何时/是否成功"）
// ---------------------------------------------------------------------------

export interface SyncState {
  ok: boolean;
  /** 时间戳（ms） */
  at: number;
  /** 结果说明（成功站点名 / 失败原因） */
  message?: string;
}

const SYNCSTATE_KEY = 'syncState';

/** 读取所有站点的最近同步结果。 */
export async function loadSyncState(): Promise<Record<string, SyncState>> {
  const result = await chrome.storage.local.get(SYNCSTATE_KEY);
  return (result[SYNCSTATE_KEY] as Record<string, SyncState>) ?? {};
}

/** 记录某站点的最近同步结果。 */
export async function recordSyncState(domain: string, state: SyncState): Promise<void> {
  const all = await loadSyncState();
  all[domain] = state;
  await chrome.storage.local.set({ [SYNCSTATE_KEY]: all });
}
