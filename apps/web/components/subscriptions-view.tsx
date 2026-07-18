"use client";

import { useCallback, useEffect, useState } from "react";

import type { Route } from "next";

import { PosterCardVisual, type PosterVisualItem } from "@/components/poster-card";
import { listSubscriptions, type Subscription } from "@/lib/api/subscriptions";
import { cachedImageUrl } from "@/lib/image-proxy";
import {
  subscriptionProgressNote,
  subscriptionStatusMeta,
} from "@/lib/subscription-ui";

/**
 * 订阅页：用户全部订阅的海报墙（数据来自 /subscriptions 接口）。
 *
 * 结构：页头（标题 + 订阅总数说明）+ 自适应海报网格。
 * 每格复用发现页的 PosterCard，并在其下追加一行订阅状态
 * （彩色状态点 + 进度说明，如「追更中 · 缺 3 集」），
 * 让用户不点进详情也能扫读全部订阅的追踪进度。
 */
export function SubscriptionsView() {
  const [mediaType, setMediaType] = useState<"movie" | "tv">("movie");
  const [subscriptions, setSubscriptions] = useState<Subscription[] | null>(null);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(() => {
    setFailed(false);
    listSubscriptions()
      .then(setSubscriptions)
      .catch(() => setFailed(true));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const visible = (subscriptions ?? []).filter((s) => s.media.kind === mediaType);

  return (
    <div className="scroll-thin flex-1 overflow-y-auto pb-10">
      <div className="flex items-start justify-between gap-4 px-6 pt-2">
        <div>
          <h2 className="text-on-image text-[26px] font-bold leading-tight tracking-[-0.02em] text-white">
            我的订阅
          </h2>
          <p className="text-on-image mt-1.5 text-[13px] text-[var(--text-muted)]">
            共 {visible.length} 部{mediaType === "movie" ? "电影" : "剧集"} ·
            movieclaw 会持续追踪并在新资源放出后自动入库
          </p>
        </div>

        <MediaTypeSwitcher value={mediaType} onChange={setMediaType} />
      </div>

      {subscriptions === null && !failed && (
        <div className="mt-16 flex items-center justify-center gap-2.5 text-[13px] text-[var(--text-muted)]">
          <span className="size-4 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
          正在加载订阅…
        </div>
      )}

      {failed && (
        <div className="mt-16 flex flex-col items-center gap-3 text-center">
          <p className="text-[13.5px] text-[var(--text-muted)]">订阅列表加载失败</p>
          <button
            type="button"
            onClick={reload}
            className="btn-glass px-4 py-2 text-[13px] font-medium text-[var(--text)]"
          >
            重试
          </button>
        </div>
      )}

      {subscriptions !== null && !failed && visible.length === 0 && (
        <p className="mt-16 text-center text-[13.5px] leading-7 text-[var(--text-muted)]">
          还没有订阅任何{mediaType === "movie" ? "电影" : "剧集"}。
          <br />
          在发现页或搜索结果里打开影片详情，点「订阅追踪」即可加入。
        </p>
      )}

      <div className="mt-6 grid gap-x-4 gap-y-7 px-6 [grid-template-columns:repeat(auto-fill,minmax(148px,1fr))]">
        {visible.map((sub) => (
          <SubscriptionCell key={sub.id} sub={sub} />
        ))}
      </div>
    </div>
  );
}

/** 订阅类型切换：沿用发现页的数据源切换样式，让同类操作保持一致。 */
function MediaTypeSwitcher({
  value,
  onChange,
}: {
  value: "movie" | "tv";
  onChange: (type: "movie" | "tv") => void;
}) {
  return (
    <div
      className="flex shrink-0 rounded-full border border-white/10 bg-black/35 p-1 backdrop-blur-xl"
      aria-label="订阅类型"
    >
      {(["movie", "tv"] as const).map((type) => (
        <button
          key={type}
          type="button"
          aria-pressed={value === type}
          onClick={() => onChange(type)}
          className={`rounded-full px-4 py-1.5 text-xs font-semibold transition ${
            value === type
              ? "bg-white/15 text-white shadow-sm"
              : "text-[var(--text-muted)] hover:text-white"
          }`}
        >
          {type === "movie" ? "电影" : "剧集"}
        </button>
      ))}
    </div>
  );
}

/** 把订阅条目摘要适配成海报卡片的视觉契约。 */
function toVisualItem(sub: Subscription): PosterVisualItem {
  return {
    id: String(sub.media.tmdb_id),
    source: "tmdb",
    title: sub.media.title,
    year: sub.media.year ?? undefined,
    rating: 0,
    posterUrl: sub.media.poster_url ? cachedImageUrl(sub.media.poster_url) : "",
  };
}

/** 海报墙单元格：点击进订阅详情分析页（追踪明细 + 活动时间线），而非影片详情。 */
function SubscriptionCell({ sub }: { sub: Subscription }) {
  const meta = subscriptionStatusMeta[sub.status];
  return (
    <div>
      <PosterCardVisual
        item={toVisualItem(sub)}
        href={`/subscriptions/${sub.id}` as Route}
      />
      <p className="text-on-image mt-1.5 flex items-center gap-1.5 truncate text-[11px] text-[var(--text-muted)]">
        <span
          className="size-1.5 shrink-0 rounded-full"
          style={{ backgroundColor: meta.color }}
        />
        <span className="truncate">
          {meta.label} · {subscriptionProgressNote(sub)}
        </span>
      </p>
    </div>
  );
}
