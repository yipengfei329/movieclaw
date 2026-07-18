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
  reactStrictMode: true,
  typedRoutes: true,
  // 关闭左下角 Next.js 开发指示器（dev tools 浮动按钮）
  devIndicators: false,
  async rewrites() {
    if (process.env.NODE_ENV !== "development" || !apiBaseUrl.startsWith("/")) {
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
