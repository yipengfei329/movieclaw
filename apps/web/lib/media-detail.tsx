"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";

import type { BreadcrumbItem } from "@/components/breadcrumb";
import type { MediaItem, MediaSource } from "@/lib/media-types";

/**
 * 影片详情的全站入口：详情是真实路由 /media/[type]/[id]，
 * 海报卡片分散在发现页 / 订阅页 / Hero 横幅 / 相似推荐等多层组件里，
 * 都通过这里的 open() 跳转，保持「点卡片开详情」的调用方式不变。
 *
 * seed 缓存：点卡片时列表里已有的字段（标题/海报/简介）先存一份，
 * 详情页挂载时立即用它渲染首屏，再等 /discover/{type}/{id} 补齐词条信息——
 * 站内跳转零白屏。硬刷新 / 分享链接直达时缓存为空，详情页转为加载态等接口。
 */
const seedCache = new Map<string, MediaItem>();

export function getMediaSeed(source: MediaSource, id: string): MediaItem | undefined {
  return seedCache.get(`${source}:${id}`);
}

/**
 * 来路缓存：点开详情时记下「从哪个列表页来」，详情页面包屑据此渲染父级——
 * 从发现页 / 搜索页 / 订阅页点进同一部影片，向上回到的就是来的那个列表
 * （含查询串原样回跳）。硬刷新 / 分享直达时缓存为空，详情页按影片类型
 * 兜底到「发现电影 / 发现剧集」。与 seedCache 同生命周期、同上限策略。
 */
const originCache = new Map<string, BreadcrumbItem[]>();

export function getMediaOrigin(source: MediaSource, id: string): BreadcrumbItem[] | undefined {
  return originCache.get(`${source}:${id}`);
}

/** 由「点开详情时所在的页面」推导来路面包屑；不认识的来路（如详情页内的相似推荐）返回 null。 */
function originTrailOf(pathname: string, search: string): BreadcrumbItem[] | null {
  const here = pathname + search;
  if (pathname === "/discover/movie") return [{ label: "发现电影", href: here }];
  if (pathname === "/discover/tv") return [{ label: "发现剧集", href: here }];
  if (pathname === "/discover/movie/top250") {
    return [
      { label: "发现电影", href: "/discover/movie" },
      { label: "豆瓣电影 Top 250", href: here },
    ];
  }
  if (pathname === "/search") {
    const keyword = new URLSearchParams(search).get("q")?.trim();
    return keyword ? [{ label: `搜索“${keyword}”`, href: here }] : null;
  }
  if (pathname.startsWith("/subscriptions")) return [{ label: "我的订阅", href: "/subscriptions" }];
  if (pathname.startsWith("/library")) return [{ label: "媒体库", href: "/library" }];
  return null;
}

export interface MediaDetailNav {
  /** 打开某部影片的详情页（种下 seed 后跳路由） */
  open: (item: MediaItem) => void;
  /** 详情页的「返回」：优先回上一页；直达链接没有站内历史时回首页 */
  close: () => void;
}

export function useMediaDetail(): MediaDetailNav {
  const router = useRouter();
  return useMemo(
    () => ({
      open(item: MediaItem) {
        if (seedCache.size > 100) {
          // 防止长会话无限增长的粗粒度上限
          seedCache.clear();
          originCache.clear();
        }
        const source = item.source ?? "tmdb";
        seedCache.set(`${source}:${item.id}`, item);
        const trail = originTrailOf(window.location.pathname, window.location.search);
        if (trail) originCache.set(`${source}:${item.id}`, trail);
        else originCache.delete(`${source}:${item.id}`);
        router.push(
          source === "douban"
            ? (`/media/douban/${item.id}` as Route)
            : (`/media/${item.type}/${item.id}` as Route),
        );
      },
      close() {
        if (window.history.length > 1) {
          router.back();
        } else {
          router.push("/");
        }
      },
    }),
    [router],
  );
}
