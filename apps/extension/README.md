# MovieClaw Cookie 同步助手（浏览器插件）

配套 MovieClaw 主程序使用的极简 Chrome 插件。核心作用：读取你正在浏览的站点的
Cookie（含 httpOnly 会话 Cookie），用于主程序的站点模拟访问。

## 技术选型

- **WXT**：基于 Vite 的现代浏览器插件框架，自动生成 Manifest V3，支持 Chrome / Firefox。
- **原生 TS + 原生 CSS**：popup 页面很简单，不引入任何 UI 框架，保持轻量。

## 权限设计（易用性优先）

只申请 `cookies`、`activeTab`、`storage` 三个权限，**不申请任何 host_permissions**，
因此安装时不会弹出"可读取网站数据"的警告。原理：用户点击插件图标这一动作，浏览器
会临时授予当前标签页的读取权限，插件即可读取该站点 Cookie。

> 使用约束：需先停留在目标站点的网页上，再点击插件图标。

## 开发调试

```bash
# 在仓库根目录
pnpm ext:dev      # 启动开发模式，自动打开带插件的 Chrome
pnpm ext:build    # 生产构建，产物在 apps/extension/.output/chrome-mv3
pnpm ext:zip      # 打包成可上传应用商店的 zip
```

## 手动加载（无需命令行）

1. 运行 `pnpm ext:build` 得到 `apps/extension/.output/chrome-mv3` 目录。
2. Chrome 打开 `chrome://extensions`，右上角开启「开发者模式」。
3. 点「加载已解压的扩展程序」，选择上述 `chrome-mv3` 目录。

## 交互设计（单弹窗，两视图）

插件只有一个弹窗，内含两个视图，通过顶栏齿轮 / 返回箭头切换：

- **主视图**：顶栏常驻「已连接 / 未配置 / 后端未连接」状态徽标；对当前站点只做两件事——
  **授权本站** →**立即同步**（同步过则显示「重新同步」与「上次同步于何时」）。不支持的站点会明确提示。
- **设置视图**（齿轮进入，随时可用，不跳转到 `chrome://extensions`）：填后端地址 + 令牌、
  测试连接、保存；以及一个**全局「自动同步」开关**。保存后就地反馈并自动返回主视图。

## 后端同步

1. 在 MovieClaw 后台「设置 → 浏览器插件」生成同步令牌（或 `POST /api/v1/extension/token`）。
2. 弹窗顶栏齿轮 → 设置视图，填入后端地址与令牌，「测试连接」→「保存」（保存时一次性申请后端地址访问权限）。
3. 在受支持且已登录的站点页面点插件图标 →（首次）授权本站 →「立即同步」。

后端接口一览（详见 `src/movieclaw_api/api/routes/extension.py`）：

- `POST /api/v1/extension/cookies` —— 推送某域名 Cookie（需令牌）
- `GET  /api/v1/extension/sites` —— 支持 cookie 同步的站点及状态（需令牌）
- `GET  /api/v1/extension/ping` —— 连接自检（需令牌）
- `GET/POST/DELETE /api/v1/extension/token` —— 令牌管理（Web 后台用）

## 自动同步（全局开关，后台执行）

设置里开启全局「自动同步」后，凡是**同步过一次的站点**都会被纳入管理，插件后台
（[background.ts](entrypoints/background.ts)）随即：

- **实时**：监听 `chrome.cookies.onChanged`，被管理站点的 Cookie 一变就【防抖 2 秒】后推送最新值；
- **启动兜底**：浏览器启动时（`onStartup`）对所有被管理站点全量推一次（关闭期间 Cookie 可能已变）；
- **定时兜底**：`alarms` 每 30 分钟全量推一次，防止漏掉的事件累积成陈旧。

后台读取 Cookie 依赖站点 host 权限、推送依赖后端源 host 权限，二者都在授权/保存配置时已获得。
同步结果记入本地，弹窗会显示「上次同步：x 分钟前」。出错时插件图标上会出现红色角标。

## 路线图

- [x] 本地读取当前站点 Cookie。
- [x] 与 MovieClaw 后端通信，一键同步 Cookie（令牌鉴权 + 域名映射 + 自动验证）。
- [x] 后台自动同步 —— 全局开关 + `chrome.cookies.onChanged` 防抖推送 + 启动/定时兜底。
- [x] 弹窗内置设置视图 + 顶栏连接状态；每站点只做授权 + 立即同步。
