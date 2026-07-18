"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ArrowLeftIcon } from "@/components/icons";
import {
  deleteSubscription,
  getSubscription,
  listRuleSets,
  listSubscriptionActivities,
  pauseSubscription,
  type RuleSet,
  type SubscriptionActivity,
  type SubscriptionDetail,
  type WantedItem,
} from "@/lib/api/subscriptions";
import { cachedImageUrl } from "@/lib/image-proxy";
import {
  subscriptionProgressNote,
  subscriptionStatusMeta,
} from "@/lib/subscription-ui";
import { formatDateTime, formatRelativeTime } from "@/lib/time";

/**
 * 订阅详情分析页（/subscriptions/[id]）：订阅透明化的落点。
 *
 * 回答三个问题：
 *   1. 我订了什么 —— 参数卡（勾选季 / 追新 / 规则组）+ 状态与进度；
 *   2. 每一集到哪一步了 —— 按季分组的追踪项明细，含调度信息
 *      （排队中 / 待播出 / 未定档 / 已提交下载），让"正在寻找资源"
 *      背后的每个单元都可见；
 *   3. 系统做过什么 —— 活动时间线，后端每个动作的中文流水
 *      （创建 / 调整 / 暂停恢复 / 收齐；P4 起：搜索 / 匹配 / 拒绝原因 / 投递）。
 */
export function SubscriptionInspectorView({ id }: { id: number }) {
  const router = useRouter();
  const [detail, setDetail] = useState<SubscriptionDetail | null>(null);
  const [activities, setActivities] = useState<SubscriptionActivity[]>([]);
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([]);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(() => {
    Promise.all([
      getSubscription(id),
      listSubscriptionActivities(id),
      listRuleSets(),
    ])
      .then(([d, acts, rules]) => {
        setDetail(d);
        setActivities(acts);
        setRuleSets(rules);
      })
      .catch(() => setFailed(true));
  }, [id]);

  useEffect(() => {
    reload();
  }, [reload]);

  const ruleSetName = useMemo(
    () => ruleSets.find((r) => r.id === detail?.rule_set_id)?.name ?? `#${detail?.rule_set_id}`,
    [ruleSets, detail],
  );

  if (failed) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4">
        <p className="text-[14px] text-[var(--text-muted)]">未能加载该订阅，可能已被删除。</p>
        <Link href="/subscriptions" className="btn-glass px-4 py-2 text-[13px] font-medium">
          <ArrowLeftIcon className="size-4" />
          返回订阅列表
        </Link>
      </div>
    );
  }

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center gap-2.5 text-[13px] text-[var(--text-muted)]">
        <span className="size-4 animate-spin rounded-full border-2 border-white/20 border-t-white/70" />
        正在加载订阅详情…
      </div>
    );
  }

  const meta = subscriptionStatusMeta[detail.status];
  const isMovie = detail.media.kind === "movie";

  const togglePause = async () => {
    setBusy(true);
    try {
      await pauseSubscription(detail.id, detail.status !== "paused");
      reload();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确定取消订阅《${detail.media.title}》？已下载的内容不受影响。`)) return;
    setBusy(true);
    try {
      await deleteSubscription(detail.id);
      router.push("/subscriptions");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scroll-thin flex-1 overflow-y-auto px-6 pb-12">
      {/* —— 页头：返回 + 条目摘要 + 操作 —— */}
      <div className="flex items-start gap-5 pt-2">
        <Link
          href="/subscriptions"
          aria-label="返回订阅列表"
          className="surface-raised mt-1 flex size-9 shrink-0 items-center justify-center !rounded-full text-[var(--text)] transition-transform hover:scale-110"
        >
          <ArrowLeftIcon className="size-4" />
        </Link>

        {detail.media.poster_url && (
          <Link
            href={`/media/${detail.media.kind}/${detail.media.tmdb_id}`}
            className="block w-[72px] shrink-0 overflow-hidden rounded-lg ring-1 ring-white/15"
          >
            <img
              src={cachedImageUrl(detail.media.poster_url)}
              alt={`${detail.media.title} 海报`}
              className="aspect-[2/3] w-full bg-[#141824] object-cover"
            />
          </Link>
        )}

        <div className="min-w-0 flex-1">
          <h2 className="text-on-image flex items-baseline gap-2.5 text-[24px] font-bold leading-tight tracking-[-0.02em] text-white">
            <Link
              href={`/media/${detail.media.kind}/${detail.media.tmdb_id}`}
              className="truncate hover:underline"
            >
              {detail.media.title}
            </Link>
            <span className="tnum shrink-0 text-[14px] font-normal text-white/50">
              {detail.media.year ?? ""}
            </span>
          </h2>
          <p className="text-on-image mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-[var(--text-muted)]">
            <span className="flex items-center gap-1.5">
              <span className="size-1.5 rounded-full" style={{ backgroundColor: meta.color }} />
              {meta.label} · {subscriptionProgressNote(detail)}
            </span>
            <span>
              {isMovie
                ? "电影"
                : detail.selected_seasons.length > 0
                  ? `勾选第 ${detail.selected_seasons.join("、")} 季`
                  : "未勾选季"}
            </span>
            {!isMovie && <span>持续追新 {detail.follow_future ? "开" : "关"}</span>}
            <span>规则组「{ruleSetName}」</span>
            <span>订阅于 {formatDateTime(detail.created_at)}</span>
          </p>
        </div>

        <div className="flex shrink-0 gap-2.5 pt-1">
          <button
            type="button"
            disabled={busy || detail.status === "completed"}
            onClick={togglePause}
            className="btn-glass h-9 px-4 text-[12.5px] font-medium disabled:opacity-40"
          >
            {detail.status === "paused" ? "恢复追踪" : "暂停"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={remove}
            className="h-9 rounded-full border border-red-400/30 bg-red-500/10 px-4 text-[12.5px] font-medium text-red-200 transition hover:bg-red-500/20 disabled:opacity-40"
          >
            取消订阅
          </button>
        </div>
      </div>

      <div className="mt-8 grid gap-8 xl:grid-cols-[minmax(0,1fr)_minmax(0,380px)]">
        {/* —— 追踪项明细：每一集到哪一步了 —— */}
        <section className="min-w-0">
          <h3 className="text-on-image mb-3 text-[15px] font-semibold text-[var(--text)]">
            追踪明细
            <span className="ml-2 text-[12px] font-normal text-[var(--text-faint)]">
              共 {detail.progress.total} 项 · 缺 {detail.progress.wanted}
            </span>
          </h3>
          <WantedBreakdown wanted={detail.wanted} isMovie={isMovie} />
        </section>

        {/* —— 活动时间线：系统做过什么 —— */}
        <section className="min-w-0">
          <h3 className="text-on-image mb-3 text-[15px] font-semibold text-[var(--text)]">
            活动记录
            <span className="ml-2 text-[12px] font-normal text-[var(--text-faint)]">
              系统对该订阅的每个动作
            </span>
          </h3>
          <div className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-5 backdrop-blur-xl">
            {activities.length === 0 ? (
              <p className="text-[12.5px] text-[var(--text-faint)]">暂无活动记录</p>
            ) : (
              <ol className="space-y-4">
                {activities.map((a) => (
                  <li key={a.id} className="flex gap-3">
                    <span
                      className="mt-1.5 size-1.5 shrink-0 rounded-full"
                      style={{ backgroundColor: activityColor(a.type) }}
                    />
                    <div className="min-w-0">
                      <p className="text-[12.5px] leading-5 text-white/85">{a.message}</p>
                      <p
                        className="tnum mt-0.5 text-[11px] text-[var(--text-faint)]"
                        title={formatDateTime(a.created_at)}
                      >
                        {formatRelativeTime(a.created_at)}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}

/** 活动类型 → 时间线圆点颜色：绿=成果，红=失败/拒绝，黄=暂停，蓝=常规动作。 */
function activityColor(type: SubscriptionActivity["type"]): string {
  switch (type) {
    case "grabbed":
    case "match_accepted":
    case "completed":
      return "#4ade80";
    case "match_rejected":
    case "dispatch_failed":
      return "#f87171";
    case "paused":
      return "#f5c451";
    default:
      return "#6aa7ff";
  }
}

/** 追踪项按季分组展开；电影是单项的退化形态。 */
function WantedBreakdown({ wanted, isMovie }: { wanted: WantedItem[]; isMovie: boolean }) {
  if (wanted.length === 0) {
    return (
      <p className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-5 text-[12.5px] leading-6 text-[var(--text-muted)]">
        当前没有追踪项。开启「持续追新」后，新集播出会自动加入。
      </p>
    );
  }

  const seasons = new Map<number, WantedItem[]>();
  for (const w of wanted) {
    const list = seasons.get(w.season_number) ?? [];
    list.push(w);
    seasons.set(w.season_number, list);
  }

  return (
    <div className="space-y-4">
      {[...seasons.entries()]
        .sort(([a], [b]) => a - b)
        .map(([season, items]) => (
          <div
            key={season}
            className="overflow-hidden rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] backdrop-blur-xl"
          >
            {!isMovie && (
              <p className="border-b border-white/[0.06] px-5 py-2.5 text-[12.5px] font-semibold text-white/80">
                {season === 0 ? "特别篇" : `第 ${season} 季`}
                <span className="ml-2 font-normal text-[var(--text-faint)]">
                  {items.filter((w) => w.status !== "wanted").length}/{items.length} 已安排
                </span>
              </p>
            )}
            <ul className="divide-y divide-white/[0.05]">
              {items.map((w) => (
                <WantedRow key={w.id} wanted={w} isMovie={isMovie} />
              ))}
            </ul>
          </div>
        ))}
    </div>
  );
}

/**
 * 单个追踪项的透明化行：状态徽标 + 该项此刻"卡在哪、下一步是什么"。
 * 文案与后端调度语义一一对应（补旧排队 / 追新等被动匹配 / 未定档不可调度）。
 */
function WantedRow({ wanted: w, isMovie }: { wanted: WantedItem; isMovie: boolean }) {
  const { label, color, note } = wantedPresentation(w);
  return (
    <li className="flex items-center gap-4 px-5 py-2.5">
      <span className="tnum w-14 shrink-0 text-[12.5px] font-medium text-white/90">
        {isMovie ? "正片" : `E${String(w.episode_number).padStart(2, "0")}`}
      </span>
      <span
        className="shrink-0 rounded-full px-2 py-0.5 text-[10.5px] font-semibold"
        style={{ backgroundColor: `${color}22`, color }}
      >
        {label}
      </span>
      <span className="tnum min-w-0 flex-1 truncate text-[12px] text-[var(--text-muted)]">
        {note}
      </span>
      {w.search_attempts > 0 && (
        <span className="tnum shrink-0 text-[11px] text-[var(--text-faint)]">
          已搜索 {w.search_attempts} 次
        </span>
      )}
    </li>
  );
}

function wantedPresentation(w: WantedItem): { label: string; color: string; note: string } {
  if (w.status === "downloaded") {
    return { label: "已入库", color: "#4ade80", note: `完成于 ${formatDateTime(w.grabbed_at)}` };
  }
  if (w.status === "grabbed") {
    return {
      label: "已提交下载",
      color: "#34d399",
      note: `${formatRelativeTime(w.grabbed_at)}提交给下载器`,
    };
  }
  // status === "wanted"：按调度语义解释它此刻卡在哪
  if (w.next_search_at === null) {
    return {
      label: "未定档",
      color: "#9ca3af",
      note: "播出日期未公布，暂不安排搜索；定档后自动排队",
    };
  }
  const due = new Date(w.next_search_at);
  if (w.air_date && new Date(w.air_date) > new Date()) {
    return {
      label: "待播出",
      color: "#f5c451",
      note: `${w.air_date} 播出；播出后优先靠新种子自动匹配，${formatDateTime(w.next_search_at)} 起兜底搜索`,
    };
  }
  if (due <= new Date()) {
    return {
      label: "排队搜索",
      color: "#6aa7ff",
      note: `已列入搜索队列${w.last_search_at ? `，上次搜索 ${formatRelativeTime(w.last_search_at)}` : "，等待搜索任务执行"}`,
    };
  }
  return {
    label: "冷却中",
    color: "#6aa7ff",
    note: `上次未找到合适资源，将于 ${formatDateTime(w.next_search_at)} 再次搜索`,
  };
}
