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
 *
 * 安装检测（Web 后台「浏览器插件」页用）：
 * - key：固定公钥 → 插件 ID 恒为 hhjihoefiocbpmnoohlkmeiaiplpadhj，
 *   无论用户从哪台机器「加载已解压的扩展程序」，ID 都不变（Chrome 由公钥推导 ID）。
 *   注意：改动此 key 会改变插件 ID，Web 端 lib/extension-install.ts 里的常量须同步更新。
 * - web_accessible_resources：暴露 movieclaw-marker.json 标记文件，Web 后台通过
 *   fetch chrome-extension://<ID>/movieclaw-marker.json 是否成功来判断插件已安装。
 *   matches 只能写具体域名或 <all_urls>，而 MovieClaw 是自部署、后台域名不可枚举，
 *   故用 <all_urls>（代价是任意网页都能探测到本插件的存在，属可接受的指纹开销）。
 */
export default defineConfig({
  manifest: {
    name: 'MovieClaw',
    description: '读取当前站点的 Cookie 并同步到 MovieClaw 主程序，用于站点模拟访问。',
    key: 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAskXlDErX8qq2gHh8+QiTIH18AdDk843BHG8FjCONMUMVILBpI0D1xGiknjpESKTmOe6e6A0/iQiIApZCWDiVY68/kpHuutm0N7Sa/8uW6Y+52Bj9tr4QmcNSz6bfI8qBW9dHY9Bzu4G9e4dMJxrMZ26EPgJ/i3NX9qibKQ4AoxLAS7hxrvGUDRsRMNVnf3X1fOtkW0BTPspzPi6sZvywVZ8vEalGdiHCsLE02umtb+rJJqUImznHuA4LgViXYuwFUHvlQJPEpQykHS2/HLib/z8Jw4o9Whk8hsLXU402brJxMxQF2qNtJ91YZZ6P0vcCF33Ju2LuxUtVg+EKIZ7aiwIDAQAB',
    permissions: ['cookies', 'activeTab', 'storage', 'alarms'],
    // 可选的站点权限：运行时按站点逐个申请，安装时零警告
    optional_host_permissions: ['*://*/*'],
    web_accessible_resources: [
      { resources: ['movieclaw-marker.json'], matches: ['<all_urls>'] },
    ],
  },
});
