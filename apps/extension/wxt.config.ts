import { defineConfig } from 'wxt';

/**
 * WXT 构建配置
 *
 * 权限设计（易用性优先，且真正可用）：
 * - cookies：读取 httpOnly 会话 Cookie（页面 document.cookie 读不到）。
 *   注意：chrome.cookies API 除了本权限，还【必须】有对应站点的 host 权限才能读到，
 *   仅有 activeTab 不够——这是踩过的坑。
 * - activeTab：用于在 popup 中读取当前标签页的 URL（无需 tabs 全局权限）。
 * - storage：保存后端地址、token、自动同步表等本地配置。
 * - alarms：后台自动同步的定时兜底（周期性重推，防止漏掉 cookies.onChanged 事件）。
 * - optional_host_permissions：把站点读取权限做成【可选权限】。安装时不申请，
 *   因此安装界面【不会】出现"读取您在所有网站上的数据"的吓人警告；只有当用户
 *   在某个站点点击"允许读取本站"时，才弹出一次仅限该站点的授权，且浏览器会记住。
 */
export default defineConfig({
  manifest: {
    name: 'MovieClaw Cookie 同步助手',
    description: '读取当前站点的 Cookie 并同步到 MovieClaw 主程序，用于站点模拟访问。',
    permissions: ['cookies', 'activeTab', 'storage', 'alarms'],
    // 可选的站点权限：运行时按站点逐个申请，安装时零警告
    optional_host_permissions: ['*://*/*'],
  },
});
