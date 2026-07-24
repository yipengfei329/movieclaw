import type { NextConfig } from "next";

function trimTrailingSlash(value: string): string {
  return value !== "/" && value.endsWith("/") ? value.slice(0, -1) : value;
}

const apiBaseUrl = trimTrailingSlash(process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "/api/v1");
const proxyTarget = trimTrailingSlash(process.env.MOVIECLAW_API_PROXY_TARGET?.trim() || "http://127.0.0.1:8000");

const nextConfig: NextConfig = {
  // 构建目录可用环境变量覆盖：并行的第二个 dev server（如会话内浏览器预览）
  // 必须使用独立目录，两个 next dev 同写 .next 会互相损坏 chunk。
  distDir: process.env.NEXT_DIST_DIR?.trim() || ".next",
  // Docker 部署用 standalone 输出：只带被引用到的依赖，镜像里无需完整 node_modules。
  output: "standalone",
  // 关闭 Next 图片优化：站内 next/image 只用于静态 logo，优化收益为零；
  // 关闭后 standalone 产物不再依赖 sharp 原生模块，前端构建产物跨 CPU 架构通用
  // （Docker 交叉构建时前端可在宿主架构原生编译，不必走 QEMU 模拟）。
  images: { unoptimized: true },
  reactStrictMode: true,
  typedRoutes: true,
  // 关闭左下角 Next.js 开发指示器（dev tools 浮动按钮）
  devIndicators: false,
  async rewrites() {
    // API 走同源路径时，由 Next 服务器反代到后端。开发和生产（单容器部署，
    // 前端进程反代到同容器内 127.0.0.1:8000 的后端）都依赖这条规则，
    // 因此不再按 NODE_ENV 区分。反代目标在构建时通过 MOVIECLAW_API_PROXY_TARGET 固化。
    if (!apiBaseUrl.startsWith("/")) {
      return [];
    }

    return [
      {
        source: `${apiBaseUrl}/:path*`,
        destination: `${proxyTarget}${apiBaseUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
