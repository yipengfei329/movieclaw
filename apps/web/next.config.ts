import type { NextConfig } from "next";

function trimTrailingSlash(value: string): string {
  return value !== "/" && value.endsWith("/") ? value.slice(0, -1) : value;
}

const apiBaseUrl = trimTrailingSlash(process.env.NEXT_PUBLIC_API_BASE_URL?.trim() || "/api/v1");
const proxyTarget = trimTrailingSlash(process.env.MOVIECLAW_API_PROXY_TARGET?.trim() || "http://127.0.0.1:8000");

const nextConfig: NextConfig = {
  reactStrictMode: true,
  typedRoutes: true,
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
