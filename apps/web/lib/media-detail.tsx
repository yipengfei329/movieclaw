"use client";

import { useMemo } from "react";
import { useRouter } from "next/navigation";
import type { Route } from "next";

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
        if (seedCache.size > 100) seedCache.clear(); // 防止长会话无限增长的粗粒度上限
        const source = item.source ?? "tmdb";
        seedCache.set(`${source}:${item.id}`, item);
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
