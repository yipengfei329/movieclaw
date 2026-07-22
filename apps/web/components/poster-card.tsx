"use client";

import type { Route } from "next";
import Link from "next/link";

import { CheckIcon, PlayIcon, StarIcon } from "@/components/icons";
import { PosterImage } from "@/components/poster-image";
import { useSubscribeEntry } from "@/components/subscribe-entry";
import { useMediaDetail } from "@/lib/media-detail";
import type { MediaItem, MediaType } from "@/lib/media-types";

/**
 * 海报卡片：发现页海报墙的最小单元（Netflix 式）。
 *
 * 结构分两层：
 *   - 海报区：2:3 竖版海报，常显「评分徽章（右上）+ 最高清晰度徽章（左上）」；
 *     hover 时整卡上浮、海报放大，底部升起渐变信息层（类型 / 简介 / 订阅影片按钮）。
 *   - 文字区：海报下方常显标题与「年份 · 规模」，保证不 hover 也能扫读海报墙。
 *
 * ranked 模式（Top 10 行）：海报左侧叠一个 Netflix 式描边大数字，
 * 数字与海报轻微重叠，靠 z-index 压在海报之下，形成「探出头」的层次。
 */
export interface PosterCardProps {
  item: MediaItem;
  /** Top 10 排名（1 起）；传入即渲染描边大数字变体 */
  rank?: number;
  /** 悬浮层的操作区变体，默认「订阅影片」 */
  action?: PosterCardAction;
}

/**
 * 悬浮层操作区的三种形态，按内容与用户的关系选择：
 * - subscribe：还没拥有（发现页/搜索），给「订阅影片」入口；该影片已存在订阅时
 *   自动切换为「已订阅」状态徽标（点击进入订阅管理弹层），状态来自
 *   SubscribeEntryProvider 的全站订阅列表，卡片自身不发请求；
 * - owned：已在媒体库（库首页最近添加、单库库存墙），订阅无意义，改为「已入库」标识；
 * - none：已订阅但尚未落地（单库页「追踪中」），再给订阅按钮是重复操作，不显示。
 */
export type PosterCardAction = "subscribe" | "owned" | "none";

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
  /** 电影/剧集；豆瓣轻量搜索结果没有该字段，订阅入口点击时补拉详情识别 */
  type?: MediaType;
  year?: number;
  genres?: string[];
  extent?: string;
  badges?: string[];
  overview?: string;
}

export function PosterCard({ item, rank, action }: PosterCardProps) {
  // 点击整卡（含 hover 信息层）进入该影片的详情页
  const { open } = useMediaDetail();
  return (
    <PosterCardVisual
      item={item}
      rank={rank}
      action={action}
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
  action = "subscribe",
}: {
  item: PosterVisualItem;
  rank?: number;
  onClick?: () => void;
  href?: Route;
  action?: PosterCardAction;
}) {
  const content = <PosterCardContent item={item} rank={rank} action={action} />;
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
  action = "subscribe",
}: {
  item: PosterVisualItem;
  rank?: number;
  action?: PosterCardAction;
}) {
  const { open: openSubscribe, subscriptionOf } = useSubscribeEntry();
  // 仅 subscribe 变体需要判断订阅状态；owned/none 场景（媒体库）不查询
  const existingSub = action === "subscribe" ? subscriptionOf(item) : undefined;
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
          <PosterImage
            src={item.posterUrl}
            alt={`${item.title} 海报`}
            className="absolute inset-0 size-full transition-transform duration-500 ease-out group-hover/card:scale-[1.06]"
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
            {action !== "none" && (
              <div className="mt-2.5 flex items-center gap-2">
                {action === "subscribe" ? (
                  /* 订阅入口（打开订阅弹层）。已订阅时切换为状态徽标，点击进入
                     同一弹层的管理态（可调整/取消订阅）。外层整卡是 button/Link，
                     内层不能再嵌 button，用 role=button 的 span 承载；
                     preventDefault 拦掉 Link 跳转，stopPropagation 拦掉整卡 onClick。 */
                  <span
                    role="button"
                    tabIndex={0}
                    aria-label={
                      existingSub ? `管理《${item.title}》的订阅` : `订阅《${item.title}》`
                    }
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      void openSubscribe(item);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        void openSubscribe(item);
                      }
                    }}
                    className={
                      existingSub
                        ? "flex h-7 items-center gap-1.5 rounded-full bg-white/[0.14] px-3 text-[11px] font-semibold text-white/90 backdrop-blur-sm transition-colors hover:bg-white/[0.22]"
                        : "btn-accent flex h-7 items-center gap-1 rounded-full px-3 text-[11px] font-semibold"
                    }
                  >
                    {existingSub ? (
                      <>
                        <CheckIcon className="size-3 text-[#4ade80]" />
                        已订阅
                      </>
                    ) : (
                      <>
                        <PlayIcon className="size-3" />
                        订阅影片
                      </>
                    )}
                  </span>
                ) : (
                  /* 已入库标识：非交互，与库存格下方的绿点语言一致 */
                  <span className="flex h-7 items-center gap-1.5 rounded-full bg-white/[0.14] px-3 text-[11px] font-semibold text-white/90 backdrop-blur-sm">
                    <span className="size-1.5 rounded-full bg-[#4ade80]" />
                    已入库
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* 文字区：常显标题 + 元信息（压在背景大图上，需 text-on-image 投影保证可读） */}
      <div className={`mt-2 ${rank ? "pl-11" : ""}`}>
        <p className="text-on-image truncate text-[13px] font-semibold text-[var(--text)]">
          {item.title}
        </p>
        {/* year 用真值判断：媒体库条目缺失年份时以 0 占位，不应显示出来 */}
        {(!!item.year || item.extent) && (
          <p className="text-on-image tnum mt-0.5 truncate text-[11px] text-[var(--text-muted)]">
            {item.year || ""}
            {!!item.year && item.extent ? " · " : ""}
            {item.extent}
          </p>
        )}
      </div>
    </>
  );
}
