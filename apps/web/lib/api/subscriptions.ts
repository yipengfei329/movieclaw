import { request } from "@/lib/http";
import type { MediaType } from "@/lib/media-types";

/** 后端统一响应信封（见 movieclaw_api.schemas.response.ApiResponse） */
interface ApiEnvelope<T> {
  success: boolean;
  code: string;
  message: string;
  data: T;
}

async function unwrap<T>(promise: Promise<ApiEnvelope<T>>): Promise<T> {
  return (await promise).data;
}

/** 订阅状态（见 movieclaw_db SubscriptionStatus）：completed 是派生值 */
export type SubscriptionStatus = "active" | "paused" | "completed";

/** 工单状态机（见 movieclaw_db WantedStatus） */
export type WantedStatus = "wanted" | "grabbed" | "downloaded" | "imported";

/** 条目摘要（见 schemas.subscription.MediaBrief） */
export interface SubscriptionMedia {
  media_item_id: number;
  kind: MediaType;
  tmdb_id: number;
  douban_id: string | null;
  title: string;
  original_title: string;
  year: number | null;
  poster_url: string | null;
  status: string | null;
}

/** 弹层季选择器的一行 */
export interface SeasonOverview {
  season_number: number;
  name: string;
  air_date: string | null;
  episode_count: number | null;
  /** 已播集数（air_date<=今天） */
  aired_count: number;
  /** 媒体库已有的集数（库存 H） */
  owned_count: number;
}

/** 豆瓣收敛歧义时的确认候选 */
export interface ResolveCandidate {
  tmdb_id: number;
  title: string;
  original_title: string;
  year: number | null;
  poster_url: string | null;
}

/** 订阅预检结果：ready 可直接渲染弹层 / ambiguous 先选候选 / not_found 无法订阅 */
export interface PrepareResult {
  status: "ready" | "ambiguous" | "not_found";
  media: SubscriptionMedia | null;
  seasons: SeasonOverview[];
  existing_subscription_id: number | null;
  /** 电影：媒体库里已有本片（弹层提示，不拦订阅） */
  movie_owned: boolean;
  candidates: ResolveCandidate[];
}

export interface SubscriptionProgress {
  total: number;
  /** 缺口：还没搞到的单元数 */
  wanted: number;
  grabbed: number;
  downloaded: number;
  /** 已整理入库（终态） */
  imported: number;
}

export interface Subscription {
  id: number;
  media: SubscriptionMedia;
  status: SubscriptionStatus;
  selected_seasons: number[];
  follow_future: boolean;
  rule_set_id: number;
  /** 入库目标库；null = 该类型的默认库 */
  library_id: number | null;
  progress: SubscriptionProgress;
  created_at: string;
  updated_at: string;
}

export interface WantedItem {
  id: number;
  season_number: number;
  episode_number: number;
  status: WantedStatus;
  air_date: string | null;
  priority: number;
  next_search_at: string | null;
  search_attempts: number;
  last_search_at: string | null;
  grabbed_at: string | null;
  downloaded_at: string | null;
  imported_at: string | null;
}

export interface SubscriptionDetail extends Subscription {
  wanted: WantedItem[];
}

export interface RuleSet {
  id: number;
  name: string;
  is_default: boolean;
  spec: Record<string, unknown>;
}

export interface PreparePayload {
  source: "tmdb" | "douban";
  kind: MediaType;
  tmdb_id?: number;
  /** 豆瓣入口：豆瓣标题 */
  title?: string;
  year?: number;
  douban_id?: string;
}

export interface CreateSubscriptionPayload {
  kind: MediaType;
  tmdb_id: number;
  selected_seasons?: number[];
  follow_future?: boolean;
  rule_set_id?: number | null;
  /** 入库目标库；缺省用该类型的默认库 */
  library_id?: number | null;
  douban_id?: string | null;
}

/** 订阅预检：建档条目并返回季集结构（打开订阅弹层时调用，幂等）。 */
export function prepareSubscription(payload: PreparePayload): Promise<PrepareResult> {
  return unwrap(
    request<ApiEnvelope<PrepareResult>>("/subscriptions/prepare", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

/** 创建订阅（同条目重复订阅幂等返回已有）。 */
export function createSubscription(
  payload: CreateSubscriptionPayload,
): Promise<SubscriptionDetail> {
  return unwrap(
    request<ApiEnvelope<SubscriptionDetail>>("/subscriptions", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

/** 订阅列表（含工单进度）。kind 缺省返回全部。 */
export function listSubscriptions(
  kind?: MediaType,
  init?: RequestInit,
): Promise<Subscription[]> {
  const query = kind ? `?kind=${kind}` : "";
  return unwrap(request<ApiEnvelope<Subscription[]>>(`/subscriptions${query}`, init));
}

/** 订阅详情（含工单明细）。 */
export function getSubscription(id: number): Promise<SubscriptionDetail> {
  return unwrap(request<ApiEnvelope<SubscriptionDetail>>(`/subscriptions/${id}`));
}

/** 修改订阅（季选择/追新/规则组，后端 diff 重算工单）。 */
export function updateSubscription(
  id: number,
  payload: {
    selected_seasons?: number[];
    follow_future?: boolean;
    rule_set_id?: number;
    /** 换入库目标库；缺省不变 */
    library_id?: number;
  },
): Promise<SubscriptionDetail> {
  return unwrap(
    request<ApiEnvelope<SubscriptionDetail>>(`/subscriptions/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  );
}

/** 暂停 / 恢复订阅。 */
export function pauseSubscription(id: number, paused: boolean): Promise<SubscriptionDetail> {
  return unwrap(
    request<ApiEnvelope<SubscriptionDetail>>(`/subscriptions/${id}/pause`, {
      method: "PATCH",
      body: JSON.stringify({ paused }),
    }),
  );
}

/** 删除订阅（不影响已下载内容）。 */
export function deleteSubscription(id: number): Promise<Record<string, never>> {
  return unwrap(
    request<ApiEnvelope<Record<string, never>>>(`/subscriptions/${id}`, {
      method: "DELETE",
    }),
  );
}

/** 订阅活动记录：message 是完整中文句子，时间线直接展示。 */
export interface SubscriptionActivity {
  id: number;
  type:
    | "created"
    | "adjusted"
    | "paused"
    | "resumed"
    | "completed"
    | "reopened"
    | "searched"
    | "match_accepted"
    | "match_rejected"
    | "grabbed"
    | "dispatch_failed"
    | "wanted_added";
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

/** 订阅活动时间线（系统对该订阅做过的每个动作，时间倒序）。 */
export function listSubscriptionActivities(
  id: number,
  limit = 100,
): Promise<SubscriptionActivity[]> {
  return unwrap(
    request<ApiEnvelope<SubscriptionActivity[]>>(
      `/subscriptions/${id}/activities?limit=${limit}`,
    ),
  );
}

/** 规则组列表（首次访问后端自动创建默认组）。 */
export function listRuleSets(init?: RequestInit): Promise<RuleSet[]> {
  return unwrap(request<ApiEnvelope<RuleSet[]>>("/rule-sets", init));
}
