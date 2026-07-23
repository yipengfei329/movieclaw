"use client";

import Link from "next/link";
import type { Route } from "next";

import { ChevronRightIcon } from "@/components/icons";

/** 面包屑单节点：href 为空或是末节点时渲染为纯文本（当前页不可点）。 */
export interface BreadcrumbItem {
  label: string;
  href?: string;
}

/**
 * 全站统一的页面面包屑（结构定位，非历史记录）。
 *
 * 设计约定：
 * - 只出现在层级 ≥ 2 的子页面顶部：一级页面（媒体库 / 我的订阅 / 发现）由侧栏
 *   高亮承担「我在哪」，再放单节点面包屑是噪音；
 * - 祖先节点可点击「向上」跳转，末节点是当前页（aria-current）；
 *   「后退」交给浏览器返回，面包屑不承担历史职责——两者动线不同；
 * - 详情页的父级按「来路」动态推导（见 lib/media-detail.tsx 的 origin 缓存），
 *   同一部影片从发现页 / 搜索页 / 订阅页点进来，向上回到的就是来的那个列表。
 *
 * 视觉：对齐全站的「深色玻璃胶囊 + 高亮当前段」控件语言（搜索页垂直选项卡 /
 * 类型切换器 / 数据源切换器同构）——祖先是可点的暗色段，当前页是亮胶囊段，
 * 段间用小箭头表达层级；不做裸文字行，那会像未完成的残留文本。
 */
export function Breadcrumb({
  items,
  className = "",
}: {
  items: BreadcrumbItem[];
  className?: string;
}) {
  if (items.length === 0) return null;
  return (
    <nav
      aria-label="面包屑"
      className={`inline-flex min-w-0 items-center gap-0.5 rounded-full bg-black/25 p-1 backdrop-blur-sm ${className}`}
    >
      {items.map((item, index) => {
        const last = index === items.length - 1;
        return (
          <span key={`${index}-${item.label}`} className="flex min-w-0 items-center gap-0.5">
            {item.href && !last ? (
              <Link
                href={item.href as Route}
                className="max-w-[18em] truncate rounded-full px-3 py-1 text-[12px] font-medium text-[rgba(243,245,249,0.72)] transition-colors hover:bg-white/[0.08] hover:text-white"
              >
                {item.label}
              </Link>
            ) : (
              <span
                aria-current={last ? "page" : undefined}
                className={`max-w-[18em] truncate rounded-full px-3 py-1 text-[12px] font-medium ${
                  last ? "bg-white/[0.14] text-white" : "text-[rgba(243,245,249,0.72)]"
                }`}
              >
                {item.label}
              </span>
            )}
            {!last && <ChevronRightIcon className="size-3 shrink-0 text-white/30" />}
          </span>
        );
      })}
    </nav>
  );
}
