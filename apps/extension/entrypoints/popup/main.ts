/**
 * Popup 逻辑：单弹窗内含「主视图（授权 + 立即同步）」与「设置视图（后端连接 + 全局自动同步）」。
 *
 * 设计要点（回应易用性反馈）：
 * - 设置随时可进：顶栏齿轮进入设置视图，不依赖当前站点、也不跳转到 chrome://extensions。
 * - 保存即时反馈：设置保存后就地提示并自动返回主视图；顶栏常驻「已连接/未配置」状态。
 * - 每个站点的核心动作只有：授权本站 → 立即同步（不再提供"复制"）。
 * - 自动同步是全局开关：同步过的站点自动纳入管理，后台随 Cookie 变化保持最新。
 *
 * 读取 Cookie 需目标站点 host 权限（可选权限，按站点申请）；访问后端需后端源 host 权限
 * （保存/测试时申请）。出于安全，不展示 Cookie 明文，仅展示条数。
 */

import { api, ensureBackendPermission, type ExtensionSiteView } from '../../lib/api';
import {
  type BackendConfig,
  isConfigured,
  loadConfig,
  loadSyncState,
  recordSyncState,
  saveConfig,
  setManagedSite,
} from '../../lib/config';
import { registrableDomain } from '../../lib/domain';

// —— DOM 引用 ——
const $connChip = document.getElementById('conn-chip') as HTMLSpanElement;
const $gearBtn = document.getElementById('gear-btn') as HTMLButtonElement;
const $backBtn = document.getElementById('back-btn') as HTMLButtonElement;
const $viewMain = document.getElementById('view-main') as HTMLElement;
const $viewSettings = document.getElementById('view-settings') as HTMLElement;

const $avatar = document.getElementById('site-avatar') as HTMLDivElement;
const $siteName = document.getElementById('site-name') as HTMLDivElement;
const $siteUrl = document.getElementById('site-url') as HTMLDivElement;
const $status = document.getElementById('cookie-status') as HTMLDivElement;
const $statusText = document.getElementById('cookie-status-text') as HTMLSpanElement;
const $actionBtn = document.getElementById('action-btn') as HTMLButtonElement;
const $actionLabel = document.getElementById('action-label') as HTMLSpanElement;
const $hint = document.getElementById('hint') as HTMLParagraphElement;

const $backendUrl = document.getElementById('backend-url') as HTMLInputElement;
const $syncToken = document.getElementById('sync-token') as HTMLInputElement;
const $autosyncToggle = document.getElementById('autosync-toggle') as HTMLInputElement;
const $saveBtn = document.getElementById('save-btn') as HTMLButtonElement;
const $settingsStatus = document.getElementById('settings-status') as HTMLParagraphElement;

// —— 状态 ——
type ActionMode = 'none' | 'grant' | 'sync' | 'go-settings';
let actionMode: ActionMode = 'none';

interface TabInfo {
  url: string;
  hostname: string;
  origin: string;
  favIconUrl?: string;
}
let tab: TabInfo | null = null; // null = 非 http(s) 页面
let config: BackendConfig = { backendUrl: '', syncToken: '', autoSyncEnabled: true };
let matchedSite: ExtensionSiteView | null = null;
let cookieHeader = '';

// —— 通用 UI 设置 ——
function setConn(text: string, kind: 'ok' | 'warn' | 'error' | 'neutral') {
  $connChip.textContent = text;
  $connChip.className = `conn-chip conn-${kind}`;
}

function setStatus(text: string, kind: 'neutral' | 'ok' | 'warn') {
  $statusText.textContent = text;
  $status.className = `pill pill-${kind}`; // 覆盖式赋值天然清掉 hidden
}

/** 隐藏 Cookie 状态徽标（当前站点没有值得展示的正文状态时，只留顶部连接徽标） */
function hidePill() {
  $status.classList.add('hidden');
}

function setHint(text: string, kind: 'info' | 'ok' | 'error' = 'info') {
  $hint.textContent = text;
  $hint.dataset.kind = kind;
}

/** 显示主按钮并设定其动作与文案 */
function showAction(mode: ActionMode, label: string, disabled = false) {
  actionMode = mode;
  $actionLabel.textContent = label;
  $actionBtn.disabled = disabled;
  $actionBtn.classList.remove('hidden');
}

/** 隐藏主按钮（当前站点无可执行动作时） */
function hideAction() {
  actionMode = 'none';
  $actionBtn.classList.add('hidden');
}

function registrablePatterns(hostname: string): string[] {
  const domain = registrableDomain(hostname);
  return [`*://${domain}/*`, `*://*.${domain}/*`];
}

function renderAvatar(hostname: string, favIconUrl?: string) {
  if (favIconUrl) {
    const img = new Image();
    img.src = favIconUrl;
    img.alt = '';
    img.onload = () => {
      $avatar.textContent = '';
      $avatar.appendChild(img);
    };
    img.onerror = () => {
      $avatar.textContent = hostname.charAt(0).toUpperCase();
    };
  } else {
    $avatar.textContent = hostname.charAt(0).toUpperCase();
  }
}

function formatSince(at: number): string {
  const min = Math.floor((Date.now() - at) / 60000);
  if (min < 1) return '刚刚';
  if (min < 60) return `${min} 分钟前`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} 小时前`;
  return new Date(at).toLocaleDateString();
}

// —— 视图切换 ——
function showSettings() {
  $backendUrl.value = config.backendUrl;
  $syncToken.value = config.syncToken;
  $autosyncToggle.checked = config.autoSyncEnabled;
  $settingsStatus.textContent = '';
  $viewMain.classList.add('hidden');
  $viewSettings.classList.remove('hidden');
  $gearBtn.classList.add('hidden');
  $backBtn.classList.remove('hidden');
}

function showMain() {
  $viewSettings.classList.add('hidden');
  $viewMain.classList.remove('hidden');
  $backBtn.classList.add('hidden');
  $gearBtn.classList.remove('hidden');
}

// —— 主流程 ——
async function init() {
  config = await loadConfig();

  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (activeTab?.url && /^https?:/.test(activeTab.url)) {
    const url = new URL(activeTab.url);
    tab = {
      url: activeTab.url,
      hostname: url.hostname,
      origin: url.origin,
      favIconUrl: activeTab.favIconUrl,
    };
    $siteName.textContent = url.hostname;
    $siteUrl.textContent = url.origin;
    renderAvatar(url.hostname, activeTab.favIconUrl);
  } else {
    tab = null;
    $avatar.textContent = '—';
    $siteName.textContent = '不支持的页面';
    $siteUrl.textContent = '';
  }

  await refresh();
}

/** 核心解析：根据后端连接与当前站点，决定连接徽标、Cookie 状态与主按钮 */
async function refresh() {
  // 1) 后端连接状态（顺带取支持的站点列表）
  let sites: ExtensionSiteView[] | null = null;
  if (!isConfigured(config)) {
    setConn('未配置', 'warn');
  } else {
    setConn('连接中…', 'neutral');
    try {
      sites = await api.listSites(config);
      setConn('已连接', 'ok');
    } catch {
      // 连接失败：主区只留顶部连接徽标，不在正文重复报错
      setConn('后端未连接', 'error');
      hidePill();
      hideAction();
      setHint('');
      return;
    }
  }

  // 2) 非 http(s) 页面：无可同步对象
  if (!tab) {
    hidePill();
    if (!isConfigured(config)) showAction('go-settings', '去设置后端');
    else hideAction();
    setHint('请在站点网页上打开本插件。');
    return;
  }

  // 3) 未配置后端：引导去设置
  if (!isConfigured(config)) {
    hidePill();
    showAction('go-settings', '去设置后端');
    setHint('先在设置里填后端地址与令牌。');
    return;
  }

  // 4) 已连接：判断当前站点是否受支持
  const domain = registrableDomain(tab.hostname);
  matchedSite = sites?.find((s) => s.domain === domain) ?? null;
  if (!matchedSite) {
    setStatus('该站点不支持同步', 'neutral');
    hideAction();
    setHint('MovieClaw 未收录该站点。');
    return;
  }

  // 5) 是否已授权读取本站？
  const authorized = await chrome.permissions.contains({
    origins: registrablePatterns(tab.hostname),
  });
  if (!authorized) {
    setStatus('需要授权访问本站', 'neutral');
    showAction('grant', '允许读取本站 Cookie');
    setHint('首次读取需授权，仅本站生效。');
    return;
  }

  // 6) 已授权：读取 Cookie
  await readAndOfferSync();
}

/** 读取当前站点 Cookie，并据此给出「立即同步 / 重新同步」按钮 */
async function readAndOfferSync() {
  if (!tab) return;
  try {
    const cookies = await chrome.cookies.getAll({ url: tab.url });
    if (cookies.length === 0) {
      setStatus('未检测到 Cookie', 'warn');
      hideAction();
      setHint('可能尚未登录该站点，请先登录后再试。');
      return;
    }
    cookieHeader = cookies.map((c) => `${c.name}=${c.value}`).join('; ');
    setStatus(`已读取 ${cookies.length} 条 Cookie`, 'ok');
    showAction('sync', matchedSite?.configured ? '重新同步' : '立即同步');

    // 展示上次同步情况（若有）
    const domain = registrableDomain(tab.hostname);
    const state = (await loadSyncState())[domain];
    setHint(state ? `上次同步：${formatSince(state.at)}` : '');
  } catch (err) {
    console.error('[MovieClaw] 读取 Cookie 失败：', err);
    setStatus('读取失败', 'warn');
    hideAction();
    setHint((err as Error).message, 'error');
  }
}

/** 申请当前站点 host 权限（用户手势中调用） */
async function grantSite() {
  if (!tab) return;
  try {
    const granted = await chrome.permissions.request({
      origins: registrablePatterns(tab.hostname),
    });
    if (!granted) {
      setHint('已取消授权，点按钮可再次尝试。');
      return;
    }
    // 读取 Cookie；读到了就顺手自动同步一次，省去用户再点一下
    await readAndOfferSync();
    if (actionMode === 'sync') await syncNow();
  } catch (err) {
    setHint(`申请权限失败：${(err as Error).message}`, 'error');
  }
}

/** 立即同步：推送当前 Cookie，并把该站点纳入自动同步管理 */
async function syncNow() {
  if (!tab || !matchedSite) return;
  showAction('sync', '同步中…', true);
  setHint('正在同步到 MovieClaw…');
  try {
    const granted = await ensureBackendPermission(config.backendUrl);
    if (!granted) {
      setHint('未授权访问后端地址，无法同步。请到设置重新保存。', 'error');
      showAction('sync', '重新同步');
      return;
    }
    const result = await api.pushCookies(config, tab.hostname, cookieHeader);
    const domain = registrableDomain(tab.hostname);
    // 纳入管理：全局自动同步开着时，后台会随 Cookie 变化保持它最新
    await setManagedSite(domain, {
      host: tab.hostname,
      siteId: result.site_id,
      displayName: result.display_name,
    });
    await recordSyncState(domain, { ok: true, at: Date.now(), message: `已同步到 ${result.display_name}` });
    setStatus(`已同步到 ${result.display_name}`, 'ok');
    showAction('sync', '重新同步');
    setHint(
      config.autoSyncEnabled ? '✓ 已同步，后续自动保持最新。' : '✓ 已同步（自动同步未开启）。',
      'ok',
    );
  } catch (err) {
    console.error('[MovieClaw] 同步失败：', err);
    setHint(`同步失败：${(err as Error).message}`, 'error');
    showAction('sync', '重新同步');
  }
}

function onAction() {
  if (actionMode === 'grant') void grantSite();
  else if (actionMode === 'sync') void syncNow();
  else if (actionMode === 'go-settings') showSettings();
}

// —— 设置视图逻辑 ——
function readForm(): BackendConfig {
  return {
    backendUrl: $backendUrl.value.trim().replace(/\/+$/, ''),
    syncToken: $syncToken.value.trim(),
    autoSyncEnabled: $autosyncToggle.checked,
  };
}

function validate(c: BackendConfig): string | null {
  if (!c.backendUrl) return '请填写后端地址';
  try {
    new URL(c.backendUrl);
  } catch {
    return '后端地址格式不正确，请以 http:// 或 https:// 开头';
  }
  if (!c.syncToken) return '请填写同步令牌';
  return null;
}

function setSettingsStatus(text: string, kind: 'info' | 'ok' | 'error' = 'info') {
  $settingsStatus.textContent = text;
  $settingsStatus.dataset.kind = kind;
}

/** 保存 = 先连一次做校验，通过才落库；失败则不保存并提示原因（测试与保存合并） */
async function onSave() {
  const form = readForm();
  const err = validate(form);
  if (err) return setSettingsStatus(err, 'error');
  $saveBtn.disabled = true;
  setSettingsStatus('正在连接…');
  try {
    if (!(await ensureBackendPermission(form.backendUrl))) {
      setSettingsStatus('未授权访问该地址', 'error');
      return;
    }
    await api.ping(form); // 连接校验；失败抛错 → 不保存
    await saveConfig(form);
    config = form;
    setSettingsStatus('✓ 已连接并保存', 'ok');
    // 稍作停留让用户看到反馈，然后返回主视图并刷新状态
    setTimeout(() => {
      showMain();
      void refresh();
    }, 700);
  } catch (e) {
    setSettingsStatus((e as Error).message, 'error');
  } finally {
    $saveBtn.disabled = false;
  }
}

// —— 事件绑定 ——
$actionBtn.addEventListener('click', onAction);
$gearBtn.addEventListener('click', showSettings);
$backBtn.addEventListener('click', () => {
  showMain();
  void refresh();
});
$saveBtn.addEventListener('click', () => void onSave());

void init();
