"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ArrowLeftIcon } from "@/components/icons";
import { Breadcrumb } from "@/components/breadcrumb";
import { usePageTitle } from "@/lib/use-page-title";
import { PosterImage } from "@/components/poster-image";
import { useSubscribeEntry } from "@/components/subscribe-entry";
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
 * 页面结构（与影片详情页同一套视觉语言）：
 *   1. Hero 氛围横幅 —— 海报重度模糊铺底产出该片专属底色（订阅接口无宽幅
 *      剧照，模糊海报是永远可用的兜底），上面放海报 / 标题 / 状态与参数徽片，
 *      底部一条「已入库 / 下载中 / 缺口」三段式进度条，操作按钮收右上；
 *   2. 标签页主体 —— 「追踪明细」与「活动记录」性质不同（一个是可变的状态
 *      快照，一个是只增的事件流水），不再左右分栏互相挤压，改为胶囊标签
 *      切换、各占全宽：
 *      - 追踪明细：按季分组的工单明细，含调度信息（排队中 / 待播出 /
 *        未定档 / 已提交下载），让「正在寻找资源」背后的每个单元都可见；
 *      - 活动记录：竖轨时间线，后端每个动作的中文流水全宽展示
 *        （创建 / 搜索 / 匹配 / 拒绝原因 / 投递 / 入库），长句不再折行成豆腐块。
 */
export function SubscriptionInspectorView({ id }: { id: number }) {
  const router = useRouter();
  // 暂停/取消订阅会改变全站订阅状态（海报卡片的「已订阅」徽标），操作后同步刷新
  const { refresh: refreshSubscriptions } = useSubscribeEntry();
  const [detail, setDetail] = useState<SubscriptionDetail | null>(null);
  const [activities, setActivities] = useState<SubscriptionActivity[]>([]);
  const [ruleSets, setRuleSets] = useState<RuleSet[]>([]);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<"wanted" | "activity">("wanted");

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
  usePageTitle(detail?.media.title);

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
  const poster = detail.media.poster_url ? cachedImageUrl(detail.media.poster_url) : null;

  const togglePause = async () => {
    setBusy(true);
    try {
      await pauseSubscription(detail.id, detail.status !== "paused");
      reload();
      refreshSubscriptions();
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确定取消订阅《${detail.media.title}》？已下载的内容不受影响。`)) return;
    setBusy(true);
    try {
      await deleteSubscription(detail.id);
      refreshSubscriptions();
      router.push("/subscriptions");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scroll-thin flex-1 overflow-y-auto px-6 pb-12 pt-3">
      {/* 面包屑：我的订阅 › 片名（向上回列表；后退交给浏览器返回） */}
      <Breadcrumb
        items={[{ label: "我的订阅", href: "/subscriptions" }, { label: detail.media.title }]}
        className="mb-3"
      />
      {/* —— 1. Hero 氛围横幅：模糊海报铺底 + 订阅摘要 —— */}
      <div className="relative overflow-hidden rounded-2xl bg-[#10131b] shadow-[0_24px_70px_-18px_rgba(0,0,0,0.62)] ring-1 ring-white/10">
        {poster && (
          <PosterImage
            src={poster}
            alt=""
            className="absolute inset-0 size-full scale-125 object-cover blur-3xl brightness-[0.55] saturate-[1.2]"
          />
        )}
        {/* 左深右浅的横向渐变：左侧文字区压暗保可读，右侧透出氛围色 */}
        <div className="absolute inset-0 bg-gradient-to-r from-[rgba(7,9,14,0.82)] via-[rgba(7,9,14,0.58)] to-[rgba(7,9,14,0.36)]" />

        <div className="relative z-10 flex items-start gap-5 p-6">
          {poster && (
            <Link
              href={`/media/${detail.media.kind}/${detail.media.tmdb_id}`}
              className="block w-[104px] shrink-0 overflow-hidden rounded-lg shadow-[0_16px_40px_rgba(0,0,0,0.5)] ring-1 ring-white/15"
            >
              <PosterImage
                src={poster}
                alt={`${detail.media.title} 海报`}
                className="aspect-[2/3] w-full object-cover"
              />
            </Link>
          )}

          <div className="min-w-0 flex-1">
            <p className="text-[11px] font-semibold tracking-[0.22em] text-[var(--accent-2)]">
              {isMovie ? "电影订阅" : "剧集订阅"}
            </p>
            <h1 className="mt-1.5 flex items-baseline gap-2.5 text-[26px] font-bold leading-tight tracking-[-0.02em] text-white">
              <Link
                href={`/media/${detail.media.kind}/${detail.media.tmdb_id}`}
                className="truncate hover:underline"
              >
                {detail.media.title}
              </Link>
              <span className="tnum shrink-0 text-[14px] font-normal text-white/50">
                {detail.media.year ?? ""}
              </span>
            </h1>

            <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px] text-white/70">
              <span className="flex items-center gap-1.5">
                <span className="size-1.5 rounded-full" style={{ backgroundColor: meta.color }} />
                {meta.label} · {subscriptionProgressNote(detail)}
              </span>
              <span className="text-white/45">订阅于 {formatDateTime(detail.created_at)}</span>
            </p>

            {/* 参数徽片：我订了什么 */}
            <div className="mt-3.5 flex flex-wrap gap-2">
              {!isMovie && (
                <ParamChip>
                  {detail.selected_seasons.length > 0
                    ? `勾选第 ${detail.selected_seasons.join("、")} 季`
                    : "未勾选季"}
                </ParamChip>
              )}
              {!isMovie && <ParamChip>持续追新 {detail.follow_future ? "开" : "关"}</ParamChip>}
              <ParamChip>规则组「{ruleSetName}」</ParamChip>
            </div>

            <ProgressStrip progress={detail.progress} />
          </div>

          <div className="flex shrink-0 gap-2.5 pt-0.5">
            <button
              type="button"
              disabled={busy || detail.status === "completed"}
              onClick={togglePause}
              className="btn-glass h-9 bg-white/10 px-4 text-[12.5px] font-medium backdrop-blur-md disabled:opacity-40"
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
      </div>

      {/* —— 2. 标签页：状态快照与事件流水分页各占全宽 —— */}
      <div className="text-on-image mt-7 flex items-center gap-1.5">
        <InspectorTab
          active={tab === "wanted"}
          onClick={() => setTab("wanted")}
          label="追踪明细"
          count={detail.progress.total}
        />
        <InspectorTab
          active={tab === "activity"}
          onClick={() => setTab("activity")}
          label="活动记录"
          count={activities.length}
        />
        <span className="ml-2 text-[12px] text-[var(--text-faint)]">
          {tab === "wanted" ? "每个追踪单元此刻到哪一步了" : "系统对该订阅的每个动作"}
        </span>
      </div>

      <div className="mt-4">
        {tab === "wanted" ? (
          <WantedBreakdown wanted={detail.wanted} isMovie={isMovie} />
        ) : (
          <ActivityTimeline activities={activities} />
        )}
      </div>
    </div>
  );
}

/** Hero 里的参数徽片：无边框纯填充胶囊（全站「无线框」原则）。 */
function ParamChip({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full bg-white/[0.09] px-2.5 py-1 text-[11.5px] text-white/75 backdrop-blur-sm">
      {children}
    </span>
  );
}

/** 三段式进度条：绿=已入库（终态）、蓝=下载中（在途）、底轨=缺口。 */
function ProgressStrip({
  progress,
}: {
  progress: SubscriptionDetail["progress"];
}) {
  const { total, wanted, grabbed, downloaded, imported } = progress;
  const denom = Math.max(total, 1);
  const inPipeline = grabbed + downloaded;
  return (
    <div className="mt-4">
      <div className="flex h-1.5 w-full max-w-[420px] overflow-hidden rounded-full bg-white/[0.12]">
        <div
          className="bg-[#4ade80]"
          style={{ width: `${(imported / denom) * 100}%` }}
        />
        <div
          className="bg-[#6aa7ff]"
          style={{ width: `${(inPipeline / denom) * 100}%` }}
        />
      </div>
      <p className="tnum mt-2 text-[12px] text-white/55">
        共 {total} 项 · 缺 {wanted}
        {inPipeline > 0 && ` · 下载中 ${inPipeline}`}
        {imported > 0 && ` · 已入库 ${imported}`}
      </p>
    </div>
  );
}

/** 标签页切换钮：与影片详情页「剧照/海报」同款胶囊，带计数。 */
function InspectorTab({
  active,
  onClick,
  label,
  count,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={`flex items-center gap-2 rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors ${
        active
          ? "bg-white/[0.14] text-white"
          : "text-[var(--text-muted)] hover:bg-white/[0.07] hover:text-[var(--text)]"
      }`}
    >
      {label}
      <span className="tnum text-[11.5px] opacity-70">{count}</span>
    </button>
  );
}

/** 活动类型 → 时间线圆点颜色：绿=成果，红=失败/拒绝，黄=暂停，蓝=常规动作。 */
function activityColor(type: SubscriptionActivity["type"]): string {
  switch (type) {
    case "grabbed":
    case "match_accepted":
    case "completed":
    case "downloaded":
    case "imported":
      return "#4ade80";
    case "match_rejected":
    case "dispatch_failed":
    case "import_failed":
      return "#f87171";
    case "paused":
      return "#f5c451";
    default:
      return "#6aa7ff";
  }
}

/**
 * 活动时间线（全宽竖轨）：圆点定性（颜色）+ 中文流水句 + 相对时间。
 * message 由后端写入时渲染成完整句子，前端不做模板拼接。
 */
function ActivityTimeline({ activities }: { activities: SubscriptionActivity[] }) {
  if (activities.length === 0) {
    return (
      <p className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-5 text-[12.5px] leading-6 text-[var(--text-muted)] backdrop-blur-xl">
        暂无活动记录。系统开始搜索、匹配或投递后，每个动作都会记录在这里。
      </p>
    );
  }
  return (
    <div className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] px-6 py-5 backdrop-blur-xl">
      <ol>
        {activities.map((a, i) => {
          const last = i === activities.length - 1;
          const color = activityColor(a.type);
          return (
            <li key={a.id} className="flex gap-4">
              {/* 竖轨：圆点 + 连接线（末条不画线） */}
              <div className="flex flex-col items-center">
                <span
                  className="mt-[7px] size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color, boxShadow: `0 0 8px ${color}55` }}
                />
                {!last && <span className="mt-1.5 w-px flex-1 bg-white/[0.08]" />}
              </div>
              <div
                className={`flex min-w-0 flex-1 items-baseline gap-5 ${last ? "" : "pb-5"}`}
              >
                <p className="min-w-0 flex-1 text-[13px] leading-6 text-white/85">{a.message}</p>
                <span
                  className="tnum shrink-0 text-[11.5px] text-[var(--text-faint)]"
                  title={formatDateTime(a.created_at)}
                >
                  {formatRelativeTime(a.created_at)}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** 追踪项按季分组展开；电影是单项的退化形态。 */
function WantedBreakdown({ wanted, isMovie }: { wanted: WantedItem[]; isMovie: boolean }) {
  if (wanted.length === 0) {
    return (
      <p className="rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.45)] p-5 text-[12.5px] leading-6 text-[var(--text-muted)] backdrop-blur-xl">
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
  if (w.status === "imported") {
    return { label: "已入库", color: "#4ade80", note: `入库于 ${formatDateTime(w.imported_at)}` };
  }
  if (w.status === "downloaded") {
    return {
      label: "已下载",
      color: "#4ade80",
      note: `完成于 ${formatDateTime(w.downloaded_at ?? w.grabbed_at)}，等待整理入库`,
    };
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
