"use client";

import type { Route } from "next";
import Link from "next/link";

import { PlayIcon, PlusIcon, StarIcon } from "@/components/icons";
import { useMediaDetail } from "@/lib/media-detail";
import type { MediaItem } from "@/lib/media-types";

/**
 * 海报卡片：发现页海报墙的最小单元（Netflix 式）。
 *
 * 结构分两层：
 *   - 海报区：2:3 竖版海报，常显「评分徽章（右上）+ 最高清晰度徽章（左上）」；
 *     hover 时整卡上浮、海报放大，底部升起渐变信息层（类型 / 简介 / 订阅与想看按钮）。
 *   - 文字区：海报下方常显标题与「年份 · 规模」，保证不 hover 也能扫读海报墙。
 *
 * ranked 模式（Top 10 行）：海报左侧叠一个 Netflix 式描边大数字，
 * 数字与海报轻微重叠，靠 z-index 压在海报之下，形成「探出头」的层次。
 */
export interface PosterCardProps {
  item: MediaItem;
  /** Top 10 排名（1 起）；传入即渲染描边大数字变体 */
  rank?: number;
}

/**
 * 海报卡片的最小视觉契约。搜索结果不含年份和类型，缺失字段保持不显示，
 * 但仍复用与发现页完全相同的海报、评分、悬浮信息层和来源标识。
 */
export interface PosterVisualItem {
  id: string;
  title: string;
  rating: number;
  posterUrl: string;
  source?: "tmdb" | "douban";
  year?: number;
  genres?: string[];
  extent?: string;
  badges?: string[];
  overview?: string;
}

export function PosterCard({ item, rank }: PosterCardProps) {
  // 点击整卡（含 hover 信息层）进入该影片的详情页
  const { open } = useMediaDetail();
  return (
    <PosterCardVisual
      item={item}
      rank={rank}
      onClick={() => open(item)}
    />
  );
}

/** 统一海报视觉组件；传入 onClick 时才渲染为可点击按钮。 */
export function PosterCardVisual({
  item,
  rank,
  onClick,
  href,
}: {
  item: PosterVisualItem;
  rank?: number;
  onClick?: () => void;
  href?: Route;
}) {
  const content = <PosterCardContent item={item} rank={rank} />;
  const interactiveClass =
    "group/card block w-full cursor-pointer rounded-2xl text-left outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-ring)]";
  if (href) {
    return (
      <Link href={href} className={interactiveClass} aria-label={`查看《${item.title}》详情`}>
        {content}
      </Link>
    );
  }
  if (!onClick) return <div className="group/card block w-full text-left">{content}</div>;
  return (
    <button type="button" onClick={onClick} className={interactiveClass}>
      {content}
    </button>
  );
}

function PosterCardContent({
  item,
  rank,
}: {
  item: PosterVisualItem;
  rank?: number;
}) {
  const badges = item.badges ?? [];
  const genres = item.genres ?? [];
  const overview = item.overview ?? "";
  return (
    <>
      <div className="relative">
        {/* Top 10 描边大数字：绝对定位在左下、贴齐海报底边，右缘塞进海报之下。
            位置与位数无关，两位数「10」也不会把布局撑开（多出的部分自然被海报盖住）。 */}
        {rank !== undefined && (
          <span
            aria-hidden="true"
            className="absolute bottom-0 left-0 z-0 select-none text-[108px] font-black leading-[0.78] tracking-[-0.08em]"
            style={{
              WebkitTextStroke: "2.5px rgba(205, 214, 230, 0.42)",
              color: "rgba(10, 11, 16, 0.55)",
            }}
          >
            {rank}
          </span>
        )}

        {/* 海报区：ranked 模式固定宽度并整体右移，把左侧空间留给探出的大数字 */}
        <div
          className={`relative z-[1] aspect-[2/3] overflow-hidden rounded-2xl bg-[#141824] shadow-[0_10px_28px_rgba(0,0,0,0.4)] ring-1 ring-white/[0.08] transition-all duration-300 ease-out group-hover/card:-translate-y-1.5 group-hover/card:shadow-[0_22px_50px_rgba(0,0,0,0.6)] group-hover/card:ring-white/25 ${
            rank !== undefined ? "ml-11 w-[144px]" : "w-full"
          }`}
        >
          <img
            src={item.posterUrl}
            alt={`${item.title} 海报`}
            loading="lazy"
            referrerPolicy="no-referrer"
            className="absolute inset-0 size-full object-cover transition-transform duration-500 ease-out group-hover/card:scale-[1.06]"
          />

          {/* 左上：资源最高清晰度徽章（无资源信息时不渲染） */}
          {badges[0] && (
            <span className="absolute left-2 top-2 rounded-md bg-black/55 px-1.5 py-0.5 text-[10px] font-bold tracking-wide text-[var(--accent)] backdrop-blur-sm">
              {badges[0]}
            </span>
          )}
          {/* 右上：评分徽章（暂无评分时不渲染，避免展示 0.0） */}
          {item.rating > 0 && (
            <span className="tnum absolute right-2 top-2 flex items-center gap-1 rounded-md bg-black/55 px-1.5 py-0.5 text-[11px] font-semibold text-white backdrop-blur-sm">
              <StarIcon className="size-3 text-[#f5c451]" />
              {item.rating.toFixed(1)}
            </span>
          )}

          {/* hover 信息层：底部渐变升起，展示类型 / 简介 / 快捷操作 */}
          <div className="absolute inset-x-0 bottom-0 translate-y-3 bg-gradient-to-t from-black/90 via-black/60 to-transparent px-3 pb-3 pt-10 opacity-0 transition-all duration-300 ease-out group-hover/card:translate-y-0 group-hover/card:opacity-100">
            {genres.length > 0 && (
              <p className="text-[11px] font-medium text-[var(--accent-2)]">
                {genres.join(" · ")}
              </p>
            )}
            {overview && (
              <p className="mt-1 line-clamp-3 text-[11px] leading-4 text-white/75">
                {overview}
              </p>
            )}
            <div className="mt-2.5 flex items-center gap-2">
              <span
                className={`btn-accent flex h-7 items-center rounded-full text-[11px] font-semibold ${
                  item.source === "douban" ? "px-2.5" : "gap-1 px-3"
                }`}
              >
                {item.source !== "douban" && <PlayIcon className="size-3" />}
                {item.source === "douban" ? "豆瓣" : "订阅追踪"}
              </span>
              <span
                className="flex size-7 items-center justify-center rounded-full border border-white/25 bg-white/10 text-white backdrop-blur-sm transition-colors hover:border-white/50"
                title="加入想看"
              >
                <PlusIcon className="size-3.5" />
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* 文字区：常显标题 + 元信息（压在背景大图上，需 text-on-image 投影保证可读） */}
      <div className={`mt-2 ${rank ? "pl-11" : ""}`}>
        <p className="text-on-image truncate text-[13px] font-semibold text-[var(--text)]">
          {item.title}
        </p>
        {(item.year !== undefined || item.extent) && (
          <p className="text-on-image tnum mt-0.5 truncate text-[11px] text-[var(--text-muted)]">
            {item.year}
            {item.year !== undefined && item.extent ? " · " : ""}
            {item.extent}
          </p>
        )}
      </div>
    </>
  );
}
