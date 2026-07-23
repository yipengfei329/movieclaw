"use client";

import { useEffect } from "react";

import { publicEnv } from "@/lib/env";

/**
 * 数据驱动的子页面标题：库名 / 片名 / 会话名 / 搜索词等要等接口返回才知道，
 * 无法在服务端 metadata 里声明——数据就绪后由本 hook 写 document.title，
 * 格式与根 layout 的 title template（`%s · 品牌名`）保持一致。
 * title 为空（数据未就绪）时不写，保留路由 metadata 的兜底标题。
 */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    if (title) document.title = `${title} · ${publicEnv.appName}`;
  }, [title]);
}
