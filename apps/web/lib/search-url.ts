import type { SearchQuery } from "@/components/search-results";
import {
  CATEGORY_LABEL,
  type SearchVertical,
  type TorrentCategory,
} from "@/lib/categories";

/**
 * 搜索页的 URL 序列化：一次搜索的全部输入（关键词 + 范围 + 快照）都编码进
 * /search 的查询参数，让结果页可刷新、可分享、可前进后退。
 *
 * 参数表：
 *   q        关键词（必填，缺失视为无效搜索）
 *   tab      垂直类别："media" = 影视条目（豆瓣）；缺失 = 站点资源（老链接兼容）
 *   label    范围的展示名（分类中文名 / 预设名；缺失 = 「全部」）
 *   cats     逗号分隔的一级分类；缺失 = 不限分类
 *   sites    逗号分隔的站点 id；缺失 = 全部站点
 *   poster   "1" = 结果页默认图览模式
 *   private  "1" = 无痕搜索（不写入服务端搜索历史）
 *   snapshot 历史快照 id：非空时结果页回放快照而非实时搜索
 *
 * tab 只决定落地时展示哪个垂直，不属于「一次种子搜索」的身份——结果页
 * 切换选项卡只改 tab、其余参数原样保留，所以 parseSearchQuery 不解析它
 * （由页面自行读取），避免切换选项卡被误判为新搜索而重新打站点。
 */
export function buildSearchPath(query: SearchQuery, vertical?: SearchVertical): string {
  const params = new URLSearchParams();
  params.set("q", query.keyword);
  if (vertical === "media") params.set("tab", "media");
  const { scope } = query;
  if (scope.label) params.set("label", scope.label);
  if (scope.categories.length > 0) params.set("cats", scope.categories.join(","));
  if (scope.siteIds.length > 0) params.set("sites", scope.siteIds.join(","));
  if (scope.posterMode) params.set("poster", "1");
  if (scope.skipHistory) params.set("private", "1");
  if (query.snapshotId != null) params.set("snapshot", String(query.snapshotId));
  return `/search?${params.toString()}`;
}

/** 从查询参数还原一次搜索；q 缺失/为空时返回 null（由页面重定向回首页）。 */
export function parseSearchQuery(params: URLSearchParams): SearchQuery | null {
  const keyword = params.get("q")?.trim();
  if (!keyword) return null;

  // 分类白名单过滤：手改 URL 塞进未知分类时静默丢弃，避免透传给后端报错
  const categories = (params.get("cats") ?? "")
    .split(",")
    .filter((c): c is TorrentCategory => c in CATEGORY_LABEL);
  const siteIds = (params.get("sites") ?? "").split(",").filter(Boolean);

  const snapshotRaw = params.get("snapshot");
  const snapshotId =
    snapshotRaw != null && /^\d+$/.test(snapshotRaw) ? Number(snapshotRaw) : undefined;

  return {
    keyword,
    scope: {
      label: params.get("label"),
      categories,
      siteIds,
      posterMode: params.get("poster") === "1",
      skipHistory: params.get("private") === "1",
    },
    snapshotId,
  };
}
