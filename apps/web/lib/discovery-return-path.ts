import type { Route } from "next";

import type { MediaSource, MediaType } from "./media-types";

const TMDB_DISCOVERY_PATH = /^\/media\/(?:movie|tv)\/\d+$/;
const DOUBAN_DISCOVERY_PATH = /^\/media\/douban\/[^/?#\s]+$/;

/** 构造发现详情的稳定站内路径，供媒体库条目页安全返回。 */
export function buildDiscoveryReturnPath(
  source: MediaSource,
  type: MediaType,
  id: string,
): Route {
  return (source === "douban" ? `/media/douban/${id}` : `/media/${type}/${id}`) as Route;
}

/**
 * 校验媒体库详情可接纳的发现页返回地址。
 *
 * TMDB 路由只接受数字 ID；豆瓣路由的 ID 是字符串，允许任意安全的单一路径段。
 * 精确比较匹配结果，避免 JavaScript 的 `$` 接受末尾换行而把非法地址放行。
 */
export function getDiscoveryReturnPath(returnTo?: string): Route | null {
  if (!returnTo) return null;
  const match = TMDB_DISCOVERY_PATH.exec(returnTo) ?? DOUBAN_DISCOVERY_PATH.exec(returnTo);
  return match?.[0] === returnTo ? (returnTo as Route) : null;
}
