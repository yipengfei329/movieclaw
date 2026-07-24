"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import type { Route } from "next";

import {
  ChevronLeftIcon,
  ChevronRightIcon,
  GlobeIcon,
  InfoIcon,
  PlayIcon,
  StarIcon,
} from "@/components/icons";
import { MediaRow } from "@/components/media-row";
import { PosterImage } from "@/components/poster-image";
import { fetchDiscoverPage } from "@/lib/api/discover";
import { HttpError } from "@/lib/http";
import { useMediaDetail } from "@/lib/media-detail";
import type {
  DiscoverPageData,
  MediaItem,
  MediaSource,
  MediaType,
} from "@/lib/media-types";

/**
 * 发现页（发现电影 / 发现剧集）：Netflix 式「Hero 大横幅 + 分类横滚行」。
 *
 * 页面纵向结构：
 *   1. HeroBanner —— 精选影片轮播大横幅（宽幅剧照 + 渐变蒙版 + 标题区 + 操作按钮）
 *   2. 若干 MediaRow —— 「今日热榜 Top 10（排名变体）/ 热门 / 高分 / …」横滚海报行
 *
 * 数据来自后端 /discover 聚合接口（数据源 TMDB，服务端缓存 30 分钟）；
 * 前端再做一层模块级内存缓存，「发现电影 ↔ 发现剧集」来回切换即时呈现，
 * 刷新页面才会重新拉取。
 */
const pageCache = new Map<string, DiscoverPageData>();

/** 加载失败信息：除文案外带上后端错误码与引导提示，驱动引导式错误态。 */
interface DiscoverErrorInfo {
  message: string;
  /** 后端统一错误码（如 UPSTREAM_UNREACHABLE = 网络级不可达） */
  code?: string;
  /** 后端给的下一步操作提示（如去网络设置配代理） */
  hint?: string;
}

/** 从任意异常提取结构化错误信息（HttpError 携带后端信封的 code/details）。 */
function toErrorInfo(err: unknown): DiscoverErrorInfo {
  if (err instanceof HttpError) {
    const payload = err.details as
      | { code?: string; details?: { service?: string; hint?: string }[] }
      | undefined;
    return {
      message: err.message,
      code: payload?.code,
      hint: payload?.details?.[0]?.hint,
    };
  }
  return { message: (err as Error)?.message || "加载失败，请稍后重试" };
}

export function DiscoverView({
  mediaType,
  source,
}: {
  mediaType: MediaType;
  source: MediaSource;
}) {
  const router = useRouter();
  const cacheKey = `${mediaType}:${source}`;
  const [page, setPage] = useState<DiscoverPageData | null>(
    () => pageCache.get(cacheKey) ?? null,
  );
  const [error, setError] = useState<DiscoverErrorInfo | null>(null);
  // 重试计数器：点「重试」时 +1，触发 effect 重新拉取
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const cached = pageCache.get(cacheKey);
    setPage(cached ?? null);
    setError(null);
    if (cached) return;

    let cancelled = false;
    fetchDiscoverPage(mediaType, source)
      .then((data) => {
        pageCache.set(cacheKey, data);
        if (!cancelled) setPage(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(toErrorInfo(err));
      });
    return () => {
      cancelled = true;
    };
  }, [cacheKey, mediaType, reloadKey, source]);

  const toolbar = (
    <div className="sticky top-0 z-20 flex items-center justify-end px-6 py-3">
      <SourceSwitcher
        value={source}
        onChange={(nextSource) => {
          if (nextSource === source) return;
          router.push(`/discover/${mediaType}?source=${nextSource}` as Route);
        }}
      />
    </div>
  );

  if (error) {
    return (
      <div className="flex flex-1 flex-col">
        {toolbar}
        <DiscoverError error={error} onRetry={() => setReloadKey((k) => k + 1)} />
      </div>
    );
  }
  if (!page) {
    return (
      <div className="flex flex-1 flex-col">
        {toolbar}
        <DiscoverSkeleton />
      </div>
    );
  }
  return (
    <div className="scroll-thin flex-1 overflow-y-auto pb-10">
      {toolbar}
      {page.hero.length > 0 && (
        <div className="px-6">
          <HeroBanner items={page.hero} />
        </div>
      )}
      <div className="mt-8 space-y-8">
        {page.rows.map((row) => {
          const isTop250 = row.id === "douban-movie_top250";
          return (
            <MediaRow
              key={row.id}
              row={isTop250 ? { ...row, items: row.items.slice(0, 10) } : row}
              moreHref={
                isTop250 ? ("/discover/movie/top250?source=douban" as Route) : undefined
              }
            />
          );
        })}
      </div>
    </div>
  );
}

/** 数据源视角切换：两个视角分别缓存，来回切换不会重复请求。 */
function SourceSwitcher({
  value,
  onChange,
}: {
  value: MediaSource;
  onChange: (source: MediaSource) => void;
}) {
  return (
      <div className="flex shrink-0 rounded-full border border-white/10 bg-black/35 p-1 backdrop-blur-xl">
        {(["tmdb", "douban"] as const).map((source) => (
          <button
            key={source}
            type="button"
            onClick={() => onChange(source)}
            className={`rounded-full px-4 py-1.5 text-xs font-semibold transition ${
              value === source
                ? "bg-white/15 text-white shadow-sm"
                : "text-[var(--text-muted)] hover:text-white"
            }`}
          >
            {source === "tmdb" ? "TMDB" : "豆瓣"}
          </button>
        ))}
      </div>
  );
}

/** 加载骨架：按真实页面布局占位（Hero 大块 + 两行海报），避免切页闪白。 */
function DiscoverSkeleton() {
  return (
    <div className="flex-1 overflow-hidden px-6 pb-10" aria-busy="true" aria-label="发现页加载中">
      <div className="h-[46vh] min-h-[320px] animate-pulse rounded-2xl bg-white/[0.05] ring-1 ring-white/10" />
      {[0, 1].map((row) => (
        <div key={row} className="mt-10">
          <div className="h-4 w-28 animate-pulse rounded bg-white/[0.08]" />
          <div className="mt-4 flex gap-4 overflow-hidden">
            {Array.from({ length: 8 }, (_, i) => (
              <div
                key={i}
                className="aspect-[2/3] w-[152px] shrink-0 animate-pulse rounded-2xl bg-white/[0.05] xl:w-[164px]"
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** 加载失败态：展示后端的中文错误信息（如未配置 TMDB Key 的引导）并提供重试。
 *
 * 网络级不可达（UPSTREAM_UNREACHABLE，含熔断快速失败）渲染引导式错误：
 * 说明原因 + 后端 hint + 「前往网络设置」按钮，替代干等骨架屏。
 */
function DiscoverError({ error, onRetry }: { error: DiscoverErrorInfo; onRetry: () => void }) {
  const unreachable = error.code === "UPSTREAM_UNREACHABLE";
  return (
    <div className="flex flex-1 items-center justify-center px-6">
      <div className="max-w-md rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-8 text-center backdrop-blur-xl">
        {unreachable && (
          <span className="icon-chip mx-auto mb-4 flex size-11 !rounded-2xl">
            <GlobeIcon className="size-5" />
          </span>
        )}
        <p className="text-[15px] font-semibold text-[var(--text)]">
          {unreachable ? "无法连接数据源" : "发现页加载失败"}
        </p>
        <p className="mt-2 break-all text-[13px] leading-6 text-[var(--text-muted)]">{error.message}</p>
        {unreachable && error.hint && (
          <p className="mt-2 text-[12px] leading-6 text-[var(--text-faint)]">{error.hint}</p>
        )}
        <div className="mt-5 flex items-center justify-center gap-3">
          {unreachable && (
            <Link
              href={"/settings/network" as Route}
              className="btn-accent flex h-9 items-center rounded-full px-5 text-[13px] font-semibold"
            >
              前往网络设置
            </Link>
          )}
          <button
            type="button"
            onClick={onRetry}
            className={`${unreachable ? "btn-glass" : "btn-accent"} h-9 rounded-full px-5 text-[13px] font-semibold`}
          >
            重试
          </button>
        </div>
      </div>
    </div>
  );
}

/** Hero 轮播间隔（毫秒） */
const HERO_INTERVAL = 8000;

/**
 * Hero 大横幅：精选影片自动轮播。
 * 所有帧常驻 DOM 叠放，靠 opacity 交叉淡入淡出（避免切换时图片重新加载闪白）；
 * 文字区跟随当前帧一起淡入。两侧箭头与右下角圆点均可手动切换并重置计时。
 */
function HeroBanner({ items }: { items: MediaItem[] }) {
  const [index, setIndex] = useState(0);

  const switchSlide = (offset: number) => {
    setIndex((current) => (current + offset + items.length) % items.length);
  };

  useEffect(() => {
    if (items.length <= 1) return;
    const timer = setInterval(() => {
      setIndex((i) => (i + 1) % items.length);
    }, HERO_INTERVAL);
    return () => clearInterval(timer);
    // index 作为依赖：手动切换后重置轮播计时，避免刚点完就被自动切走
  }, [items.length, index]);

  return (
    <div className="group relative h-[46vh] min-h-[320px] w-full overflow-hidden rounded-2xl shadow-[0_24px_70px_-18px_rgba(0,0,0,0.62)] ring-1 ring-white/10">
      {items.map((item, i) => (
        <HeroSlide key={item.id} item={item} active={i === index} />
      ))}

      {/* 手动切换按钮：首尾相连，触摸设备也保留足够大的点击区域。 */}
      {items.length > 1 && (
        <>
          <button
            type="button"
            aria-label="切换到上一部"
            onClick={() => switchSlide(-1)}
            className="absolute left-3 top-1/2 z-10 flex size-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/35 text-white/80 opacity-100 ring-1 ring-white/15 backdrop-blur-sm transition hover:bg-black/55 hover:text-white focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 sm:opacity-0 sm:group-hover:opacity-100"
          >
            <ChevronLeftIcon className="size-6" />
          </button>
          <button
            type="button"
            aria-label="切换到下一部"
            onClick={() => switchSlide(1)}
            className="absolute right-3 top-1/2 z-10 flex size-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/35 text-white/80 opacity-100 ring-1 ring-white/15 backdrop-blur-sm transition hover:bg-black/55 hover:text-white focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/80 sm:opacity-0 sm:group-hover:opacity-100"
          >
            <ChevronRightIcon className="size-6" />
          </button>
        </>
      )}

      {/* 轮播指示点 */}
      {items.length > 1 && (
        <div className="absolute bottom-4 right-5 z-10 flex gap-1.5">
          {items.map((item, i) => (
            <button
              key={item.id}
              type="button"
              aria-label={`切换到《${item.title}》`}
              onClick={() => setIndex(i)}
              className={`h-1.5 rounded-full transition-all duration-300 ${
                i === index ? "w-5 bg-white/85" : "w-1.5 bg-white/30 hover:bg-white/55"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function HeroSlide({ item, active }: { item: MediaItem; active: boolean }) {
  const { open } = useMediaDetail();
  return (
    <div
      aria-hidden={!active}
      className={`absolute inset-0 transition-opacity duration-700 ease-out ${
        active ? "z-[1] opacity-100" : "z-0 opacity-0"
      }`}
    >
      {/* 宽幅剧照 + 双层渐变蒙版：左侧压暗保文字可读，底部渐隐融入页面 */}
      <PosterImage
        src={item.backdropUrl}
        alt={`${item.title} 剧照`}
        className={`absolute inset-0 size-full object-cover object-top transition-transform duration-[9000ms] ease-linear ${
          active ? "scale-[1.06]" : "scale-100"
        }`}
      />
      <div className="absolute inset-0 bg-gradient-to-r from-[rgba(7,9,14,0.88)] via-[rgba(7,9,14,0.42)] to-transparent" />
      <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-[rgba(7,9,14,0.72)] to-transparent" />

      {/* 文字与操作区：随当前帧轻微上移淡入 */}
      <div
        className={`absolute inset-0 flex max-w-xl flex-col justify-end p-7 transition-all delay-150 duration-500 ease-out sm:p-9 ${
          active ? "translate-y-0 opacity-100" : "translate-y-3 opacity-0"
        }`}
      >
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-[var(--accent-2)]">
          今日精选 · {item.type === "movie" ? "电影" : "剧集"}
        </p>
        <h2 className="text-on-image mt-2 text-[34px] font-bold leading-[1.1] tracking-[-0.02em] text-white sm:text-[40px]">
          {item.title}
        </h2>
        <p className="text-on-image mt-1 text-[13px] text-white/55">{item.originalTitle}</p>

        {/* 元信息行：评分 / 年份 / 类型 / 规模 / 质量徽章（空字段不占位） */}
        <div className="tnum mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[12.5px] text-white/80">
          {item.rating > 0 && (
            <span className="flex items-center gap-1 font-semibold text-white">
              <StarIcon className="size-3.5 text-[#f5c451]" />
              {item.rating.toFixed(1)}
            </span>
          )}
          <span>{item.year}</span>
          {item.genres.length > 0 && <span>{item.genres.join(" / ")}</span>}
          {item.extent && <span>{item.extent}</span>}
          {item.badges.length > 0 && (
            <span className="flex gap-1.5">
              {item.badges.map((b) => (
                <span
                  key={b}
                  className="rounded border border-white/25 px-1.5 py-px text-[10px] font-semibold tracking-wide text-white/85"
                >
                  {b}
                </span>
              ))}
            </span>
          )}
        </div>

        <p className="text-on-image mt-3 line-clamp-2 max-w-lg text-[13px] leading-6 text-white/75 sm:line-clamp-3">
          {item.overview}
        </p>

        <div className="mt-5 flex items-center gap-3">
          <button
            type="button"
            className="btn-accent flex h-10 items-center gap-2 rounded-full px-5 text-[13px] font-semibold"
          >
            <PlayIcon className="size-4" />
            订阅追踪
          </button>
          <button
            type="button"
            onClick={() => open(item)}
            className="btn-glass h-10 bg-white/10 px-5 text-[13px] font-medium backdrop-blur-md"
          >
            <InfoIcon className="size-4" />
            更多信息
          </button>
        </div>
      </div>
    </div>
  );
}
