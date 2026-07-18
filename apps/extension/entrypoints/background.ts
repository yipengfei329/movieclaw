/**
 * 后台自动同步（MV3 service worker）。
 *
 * 模型：用户在弹窗里对某站点点「立即同步」后，该站点即被【纳入管理】（记入 managedSites）。
 * 只要设置里的【全局自动同步开关】开着，被管理的站点一旦 Cookie 变化，后台就把最新
 * Cookie 推送到 MovieClaw，做到"已同步的站点始终保持最新"。
 *
 * MV3 生命周期与设计取舍：
 * - service worker 短命（空闲约 30 秒回收），但监听的事件会自动唤醒它，故监听器必须
 *   在顶层同步注册（本文件在 defineBackground 回调里注册即满足）。
 * - cookies.onChanged 触发极频繁，故用【内存缓存】(管理表 + 全局开关) 做快速过滤，
 *   命中后按站点【防抖】合并连续变化，避免打爆后端。
 * - 事件可能漏（浏览器关闭期间的变化、SW 冷启动窗口），再加两道兜底：启动时全量推、
 *   alarms 周期性全量推。
 */

import { api } from '../lib/api';
import {
  type BackendConfig,
  type ManagedSitesMap,
  isConfigured,
  loadConfig,
  loadManagedSites,
  recordSyncState,
} from '../lib/config';
import { readCookieHeader } from '../lib/cookies';
import { registrableDomain } from '../lib/domain';

/** 防抖等待：合并这段时间内对同一站点的连续 Cookie 变化 */
const DEBOUNCE_MS = 2000;
/** 定时兜底的周期（分钟） */
const PERIODIC_MINUTES = 30;
const ALARM_NAME = 'movieclaw-periodic-sync';

export default defineBackground(() => {
  /** 每个可注册域名的防抖计时器 */
  const timers = new Map<string, ReturnType<typeof setTimeout>>();

  /** 内存缓存：已管理站点表 + 全局自动同步开关，减少高频事件下的 storage 读取 */
  let managedCache: ManagedSitesMap | null = null;
  let autoEnabledCache: boolean | null = null;

  async function getManaged(): Promise<ManagedSitesMap> {
    if (managedCache) return managedCache;
    managedCache = await loadManagedSites();
    return managedCache;
  }

  async function getAutoEnabled(): Promise<boolean> {
    if (autoEnabledCache !== null) return autoEnabledCache;
    autoEnabledCache = (await loadConfig()).autoSyncEnabled;
    return autoEnabledCache;
  }

  // storage 变化时刷新内存缓存，保证开关/管理表即时生效
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local') return;
    if (changes.managedSites) managedCache = (changes.managedSites.newValue as ManagedSitesMap) ?? {};
    if (changes.backendConfig) {
      autoEnabledCache = (changes.backendConfig.newValue as BackendConfig | undefined)?.autoSyncEnabled ?? true;
    }
  });

  /** 防抖调度：同一站点的连续变化只在安静 DEBOUNCE_MS 后推一次 */
  function schedule(domain: string) {
    const prev = timers.get(domain);
    if (prev) clearTimeout(prev);
    timers.set(
      domain,
      setTimeout(() => {
        timers.delete(domain);
        void pushDomain(domain);
      }, DEBOUNCE_MS),
    );
  }

  /** 读取某被管理站点的最新 Cookie 并推送到后端，结果记入 storage 供弹窗展示 */
  async function pushDomain(domain: string): Promise<void> {
    const config = await loadConfig();
    if (!isConfigured(config)) return;

    const entry = (await getManaged())[domain];
    if (!entry) return;

    try {
      const cookie = await readCookieHeader(`https://${entry.host}/`);
      if (!cookie) {
        await recordSyncState(domain, {
          ok: false,
          at: Date.now(),
          message: '未读到 Cookie（可能已在浏览器中退出登录）',
        });
        return;
      }
      const result = await api.pushCookies(config, entry.host, cookie);
      await recordSyncState(domain, {
        ok: true,
        at: Date.now(),
        message: `已同步到 ${result.display_name}`,
      });
      setErrorBadge(false);
    } catch (err) {
      console.error('[MovieClaw] 自动同步失败：', domain, err);
      await recordSyncState(domain, {
        ok: false,
        at: Date.now(),
        message: (err as Error).message,
      });
      setErrorBadge(true);
    }
  }

  /** 全量推送所有被管理站点（启动/定时兜底用；受全局开关约束） */
  async function syncAll(): Promise<void> {
    if (!(await getAutoEnabled())) return;
    const managed = await loadManagedSites();
    managedCache = managed;
    for (const domain of Object.keys(managed)) {
      await pushDomain(domain);
    }
  }

  /** 出错时在插件图标上打个红色小角标，成功则清除 */
  function setErrorBadge(hasError: boolean): void {
    chrome.action.setBadgeText({ text: hasError ? '!' : '' });
    if (hasError) chrome.action.setBadgeBackgroundColor({ color: '#dc2626' });
  }

  // ---- 事件注册（顶层同步注册，保证能唤醒 SW）----------------------------

  // 1) 实时：任意站点 Cookie 变化 → 全局开关开着且命中管理表则防抖推送
  chrome.cookies.onChanged.addListener((info) => {
    void (async () => {
      if (!(await getAutoEnabled())) return;
      const domain = registrableDomain(info.cookie.domain);
      if ((await getManaged())[domain]) schedule(domain);
    })();
  });

  // 2) 兜底：浏览器启动时全量推一次（关闭期间 Cookie 可能已变）
  chrome.runtime.onStartup.addListener(() => void syncAll());

  // 3) 兜底：周期性全量推送，防止漏掉的事件累积成陈旧
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (alarm.name === ALARM_NAME) void syncAll();
  });

  // 安装/更新与每次 SW 启动时确保周期任务存在（同名 create 会覆盖，幂等）
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: PERIODIC_MINUTES });
});
