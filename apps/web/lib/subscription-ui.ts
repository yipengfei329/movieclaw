import type { Subscription, SubscriptionStatus } from "@/lib/api/subscriptions";

/**
 * 订阅状态的展示元数据与进度文案（订阅页海报墙、详情页操作区共用）。
 * 颜色语义：蓝=追踪中、绿=已收齐、黄=已暂停。
 */
export const subscriptionStatusMeta: Record<
  SubscriptionStatus,
  { label: string; color: string }
> = {
  active: { label: "追踪中", color: "#6aa7ff" },
  completed: { label: "已收齐", color: "#4ade80" },
  paused: { label: "已暂停", color: "#f5c451" },
};

/** 进度说明：回答「还缺多少」——订阅信息里最高频的一眼答案。 */
export function subscriptionProgressNote(sub: Subscription): string {
  const { wanted, grabbed, downloaded, total } = sub.progress;
  if (sub.status === "paused") return "暂停追踪";
  if (wanted === 0) {
    if (sub.status === "active") return "等待新集播出";
    return sub.media.kind === "movie" ? "已提交下载" : `全部 ${total} 集已安排`;
  }
  const got = grabbed + downloaded;
  if (sub.media.kind === "movie") return "正在寻找资源";
  return got > 0 ? `缺 ${wanted} 集 · 已入库 ${got}` : `缺 ${wanted} 集`;
}
