# Movieclaw Web 控制台基础架子方案

## Summary

采用 `单仓、多终端可扩展` 的最佳实践：保留当前 Python/FastAPI 后端不动，在仓库内新增独立前端应用 `apps/web`，而不是现在拆成第二个仓库。  
这次按你选的“最小可跑版”实现，但结构按未来 `Web / Desktop / Mobile` 扩展来设计。

技术路线默认定为：

- `Next.js App Router + TypeScript + Tailwind CSS`
- Web 作为独立客户端工程，`前后端分离 / SPA-静态优先`
- 当前不把业务逻辑放进 Next 服务端层，不依赖 SSR、Server Actions、Route Handlers
- 当前不引入 Turborepo，先用 `pnpm workspace` 做轻量 monorepo 基座；等出现第二个前端 app 或共享 TS 包时再补 `turbo`

## Key Changes

### 1. 仓库结构

新增但不迁移现有 Python 代码：

- 根目录继续保留 `pyproject.toml`、`src/`、`tests/`
- 新增 `apps/web` 作为独立 Next.js 应用
- 根目录新增最小前端工作区文件：`package.json`、`pnpm-workspace.yaml`
- 更新 `.gitignore`，补充 `node_modules`、`.next`、`out` 等前端产物

建议结构：

```text
movieclaw/
├── pyproject.toml
├── package.json
├── pnpm-workspace.yaml
├── src/
├── tests/
└── apps/
    └── web/
        ├── package.json
        ├── next.config.ts
        ├── tsconfig.json
        ├── postcss.config.mjs
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx
        │   ├── globals.css
        │   └── health/page.tsx
        ├── lib/
        │   ├── env.ts
        │   ├── http.ts
        │   └── api/
        │       └── health.ts
        └── public/
```

### 2. 前端基线能力

`apps/web` 只搭基础骨架，不做完整后台系统：

- 使用 `App Router`
- 使用 `TypeScript strict`
- 使用 `Tailwind CSS` 作为样式体系
- 提供一个最小控制台壳子：基础布局、首页、健康检查页
- 首页展示 Movieclaw 品牌入口和后续功能占位
- 健康检查页调用后端 `/api/v1/health`，验证前后端联通路径
- API 层统一从 `lib/http.ts` 和 `lib/api/*` 走，避免页面直接写 fetch
- 样式上先建立 CSS 变量和基础 design tokens，后续再接组件库时不推翻

### 3. 接口与配置约定

这次不修改 Python API 对外契约，只新增前端自己的配置约定。

新增前端公开环境变量接口：

- `NEXT_PUBLIC_API_BASE_URL`
  - 默认值选 `/api/v1`
  - 这样 Web、桌面端、移动端都按“消费后端 API”方式工作
- `NEXT_PUBLIC_APP_NAME`
  - 默认值选 `movieclaw console`

前端请求约定：

- 页面层不直接拼接后端 URL
- 所有请求通过 `lib/http.ts`
- 默认同域访问，便于后续统一 Docker / 反向代理
- 当前阶段不做鉴权方案落地，只保留后续接入入口

### 4. 构建与部署取向

当前脚手架按“开发独立、部署可合并”设计：

- 本地开发：
  - Python API 继续独立跑在 `8000`
  - Next.js 开发服务独立跑在 `3000`
- 生产部署：
  - 目标是前端可静态构建后与 Python 一起进一个 Docker
  - 未来推荐用多阶段构建：
    - Node 阶段构建前端静态产物
    - Python 阶段运行 FastAPI
    - 由 Nginx 或 FastAPI 静态托管前端资源
- 这次不把 Docker 方案做满，只确保目录和 env 契约不会阻碍后续合并镜像

## Public Interfaces / Types

新增但保持极简：

- 前端 env 类型定义：约束 `NEXT_PUBLIC_*` 配置
- 前端 HTTP 客户端封装：统一错误处理入口
- 前端 `HealthResponse` 类型，与后端 `/api/v1/health` 响应对齐
- 不新增跨端共享包；等未来出现桌面端/移动端后，再决定是否抽 `packages/contracts` 或 OpenAPI codegen

## Test Plan

实现后至少验证这些场景：

1. 前端依赖安装、类型检查、构建可通过
   - `pnpm install`
   - `pnpm --filter web lint`
   - `pnpm --filter web build`

2. 本地开发可启动
   - Python API 正常启动
   - Web dev server 正常启动
   - 首页可访问
   - `/health` 页面可正常请求后端健康接口

3. 环境变量回退策略生效
   - 未设置 `NEXT_PUBLIC_API_BASE_URL` 时默认走 `/api/v1`
   - 设置自定义 API 基地址后，请求地址正确切换

4. 前后端边界清晰
   - 页面组件不直接访问后端绝对地址
   - 无 Next.js 服务端专属业务逻辑耦合

## Assumptions And Defaults

- 仓库策略采用“`当前单仓 + 前端独立工程 + 未来可继续扩展 apps`”，不现在拆第二仓库。
- 这次只做“最小可跑版”，不包含登录、权限、复杂表格、主题系统、状态管理框架。
- 先不引入 `shadcn/ui`、`Radix`、`TanStack Query` 等增强层，避免最小脚手架过重；后续如果开始做真实控制台页面，再补最合适。
- 先不上 `turbo`；等第二个 TS app 或共享包出现后再引入，更符合当前复杂度。
- 当前机器的 Node 运行时有本地动态库问题，实施前需要先修复本地 Node/pnpm 环境，或者直接用容器化 Node 工具链生成和构建前端。
- 版本主线按当前官方文档走：
  - Next.js 使用 `App Router` 主线：[Next.js Docs](https://nextjs.org/docs)
  - Tailwind CSS 使用官方 Next.js 安装方式（`@tailwindcss/postcss` + `@import "tailwindcss"`）： [Tailwind CSS Next.js Guide](https://tailwindcss.com/docs/installation/framework-guides/nextjs)
