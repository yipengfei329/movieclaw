"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Route } from "next";
import Link from "next/link";

import { ChevronLeftIcon, ChevronRightIcon } from "@/components/icons";
import { PosterCard } from "@/components/poster-card";
import type { MediaRowData } from "@/lib/media-types";

/**
 * 横滚海报行（Netflix 式分类行）。
 *
 * 交互设计：
 *   - 隐藏原生滚动条（.scroll-none），左右两枚玻璃圆钮在整行 hover 时浮现，
 *     点击按约 85% 视口宽度平滑翻页；触控板/滚轮横扫仍然可用。
 *   - 到达边缘时对应方向的按钮隐藏（用 onScroll 实时追踪滚动位置）。
 *   - ranked 行（Top 10）的卡片更宽，为左侧描边大数字留出空间。
 */
export function MediaRow({ row, moreHref }: { row: MediaRowData; moreHref?: Route }) {
  const scrollerRef = useRef<HTMLDivElement>(null);
  const [canLeft, setCanLeft] = useState(false);
  const [canRight, setCanRight] = useState(false);

  /** 根据当前滚动位置更新两侧按钮的可用性（含 1px 容差，避免亚像素误差） */
  const updateEdges = useCallback(() => {
    const el = scrollerRef.current;
    if (!el) return;
    setCanLeft(el.scrollLeft > 1);
    setCanRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 1);
  }, []);

  // 初始与窗口尺寸变化时重算（行内容宽度随视口变化）
  useEffect(() => {
    updateEdges();
    window.addEventListener("resize", updateEdges);
    return () => window.removeEventListener("resize", updateEdges);
  }, [updateEdges]);

  const page = (dir: -1 | 1) => {
    const el = scrollerRef.current;
    el?.scrollBy({ left: dir * el.clientWidth * 0.85, behavior: "smooth" });
  };

  return (
    <section className="group/row relative">
      <div className="mb-3 flex items-center justify-between gap-4 px-6">
        <h3 className="text-on-image text-[15px] font-semibold tracking-[-0.01em] text-[var(--text)]">
          {row.title}
        </h3>
        {moreHref && (
          <Link
            href={moreHref}
            className="shrink-0 text-xs font-semibold text-[var(--text-muted)] transition hover:text-[var(--text)]"
          >
            查看完整榜单
          </Link>
        )}
      </div>

      <div className="relative">
        <div
          ref={scrollerRef}
          onScroll={updateEdges}
          // 注意不能加 scroll-snap：snap 的回吸会和 scrollBy 的平滑动画互相抵消，导致箭头点击无效
          className="scroll-none flex gap-4 overflow-x-auto px-6 pb-1 pt-1"
        >
          {row.items.map((item, i) => (
            <div
              key={`${row.id}-${item.id}`}
              className={`shrink-0 ${
                row.ranked ? "w-[188px]" : "w-[152px] xl:w-[164px]"
              }`}
            >
              <PosterCard item={item} rank={row.ranked ? i + 1 : undefined} />
            </div>
          ))}
        </div>

        {/* 左右翻页钮：行 hover 时浮现；到边缘后隐藏 */}
        <RowArrow dir={-1} visible={canLeft} onClick={() => page(-1)} />
        <RowArrow dir={1} visible={canRight} onClick={() => page(1)} />
      </div>
    </section>
  );
}

function RowArrow({
  dir,
  visible,
  onClick,
}: {
  dir: -1 | 1;
  visible: boolean;
  onClick: () => void;
}) {
  const Icon = dir === -1 ? ChevronLeftIcon : ChevronRightIcon;
  return (
    <button
      type="button"
      aria-label={dir === -1 ? "向左滚动" : "向右滚动"}
      onClick={onClick}
      // !absolute：.surface-raised 自带 position:relative 且声明在工具类之后，
      // 会盖掉普通 absolute，导致按钮掉出定位流、堆到行底部
      className={`surface-raised !absolute top-[38%] z-10 flex size-9 -translate-y-1/2 items-center justify-center !rounded-full text-[var(--text)] transition-all duration-200 hover:scale-110 ${
        dir === -1 ? "left-2" : "right-2"
      } ${
        visible
          ? "pointer-events-auto opacity-0 group-hover/row:opacity-100"
          : "pointer-events-none opacity-0"
      }`}
    >
      <Icon className="size-4" />
    </button>
  );
}
