"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import type { Route } from "next";

import { MediaSearchResults } from "@/components/media-search-results";
import { SearchResults, type SearchQuery } from "@/components/search-results";
import {
  CATEGORY_LABEL,
  SCOPE_ALL,
  scopeOfTab,
  type SearchScope,
  type SearchTab,
  type SearchVertical,
} from "@/lib/categories";
import { useSearchPrefs } from "@/lib/search-prefs";
import { buildSearchPath, parseSearchQuery } from "@/lib/search-url";
import { usePageTitle } from "@/lib/use-page-title";

/**
 * 搜索结果页（/search?q=…）：查询全部输入都在 URL 里，可刷新 / 分享 / 前进后退。
 * q 缺失或为空时（如手改地址删掉 q）重定向回首页，不渲染空结果。
 *
 * Google 式垂直选项卡：顶部「影视 | 站点资源」，关键词跟着选项卡走（URL 的
 * tab 参数，见 lib/search-url）。两个垂直惰性挂载 + 切换保活：
 *   - 惰性：站点资源的跨站搜索是秒级重操作，只有用户真正切到该选项卡才发起，
 *     媒体优先的默认落地不会打扰任何 PT 站点；
 *   - 保活：切走的垂直用 display 隐藏而非卸载，PT 流式搜索照常进行、
 *     已出的结果保留，切回来不重新搜索。
 * tab 参数被刻意排除在种子搜索的身份（torrentKey）之外——切换选项卡只改
 * tab，torrent query 引用不变，SearchResults 的搜索 effect 不会重新触发。
 */
export default function SearchPage() {
  const router = useRouter();
  const params = useSearchParams();
  // useSearchParams 返回的对象每次渲染同引用变化，这里以序列化串为依赖稳定 query
  const key = params.toString();
  const vertical: SearchVertical = params.get("tab") === "media" ? "media" : "torrent";
  // 种子搜索的身份串：剥掉 tab，切换选项卡时保持不变
  const torrentKey = useMemo(() => {
    const p = new URLSearchParams(key);
    p.delete("tab");
    return p.toString();
  }, [key]);
  const query = useMemo(
    () => parseSearchQuery(new URLSearchParams(torrentKey)),
    [torrentKey],
  );
  usePageTitle(query ? `搜索“${query.keyword}”` : null);

  if (!query) {
    if (typeof window !== "undefined") router.replace("/");
    return null;
  }

  /** 切换垂直：只改 tab 参数，范围/排序原样保留，切回时状态不丢；snapshot 例外——
   *  快照属于打开它的那个垂直（媒体/种子快照是两套数据），切到另一边一律实时搜索。
   *  读 window.location.search 而非 useSearchParams——排序/图览是用原生
   *  replaceState 写回地址栏的（不触发路由），只有前者能拿到它们的最新值。 */
  const switchVertical = (target: SearchVertical) => {
    const p = new URLSearchParams(window.location.search);
    if (target === "media") p.set("tab", "media");
    else p.delete("tab");
    p.delete("snapshot");
    router.push(`/search?${p.toString()}` as Route);
  };

  // 快照提示条的「重新搜索」：切回实时搜索（丢掉 snapshot 参数）
  const handleResearch = (keyword: string, scope: SearchScope) => {
    router.push(buildSearchPath({ keyword, scope }) as Route);
  };

  /**
   * 站点资源页切换搜索分类：关键词不变，按目标分类生成一条新的实时搜索 URL。
   * buildSearchPath 会带上预设的站点、图览和无痕设置，并自然移除旧快照。
   */
  const switchScope = (scope: SearchScope) => {
    router.push(buildSearchPath({ keyword: query.keyword, scope }) as Route);
  };

  return (
    // key = 种子搜索身份串：换关键词/范围即整体重挂载，重置两个垂直的访问状态；
    // 只切 tab 时身份不变，保活生效
    <SearchVerticals
      key={torrentKey}
      query={query}
      vertical={vertical}
      onSwitch={switchVertical}
      onScopeSwitch={switchScope}
      onResearch={handleResearch}
    />
  );
}

const VERTICAL_TABS: { id: SearchVertical; label: string }[] = [
  { id: "media", label: "影视" },
  { id: "torrent", label: "站点资源" },
];

/** 垂直选项卡 + 两个结果视图的挂载/显隐调度（惰性挂载、切换保活，见页头注释）。 */
function SearchVerticals({
  query,
  vertical,
  onSwitch,
  onScopeSwitch,
  onResearch,
}: {
  query: SearchQuery;
  vertical: SearchVertical;
  onSwitch: (target: SearchVertical) => void;
  onScopeSwitch: (scope: SearchScope) => void;
  onResearch: (keyword: string, scope: SearchScope) => void;
}) {
  const { visibleTabs } = useSearchPrefs();
  // 各垂直是否已被访问过：访问过才挂载、之后保活
  const [visited, setVisited] = useState<Record<SearchVertical, boolean>>(() => ({
    media: vertical === "media",
    torrent: vertical === "torrent",
  }));
  useEffect(() => {
    setVisited((prev) => (prev[vertical] ? prev : { ...prev, [vertical]: true }));
  }, [vertical]);

  return (
    <div className="flex h-full flex-col">
      {/* 垂直选项卡（Google 式「综合/图片」）：深色胶囊分段，压在背景大图上 */}
      <div className="shrink-0 px-6 pt-5">
        <div className="flex flex-wrap items-center gap-2">
          <div
            role="tablist"
            aria-label="搜索垂直类别"
            className="inline-flex gap-1 rounded-full bg-black/25 p-1 backdrop-blur-sm"
          >
            {VERTICAL_TABS.map((tab) => {
              const active = tab.id === vertical;
              return (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  aria-selected={active}
                  onClick={() => !active && onSwitch(tab.id)}
                  className={`rounded-full px-3.5 py-1 text-[12px] font-medium transition-colors ${
                    active
                      ? "bg-white/[0.16] text-white"
                      : "text-[rgba(243,245,249,0.72)] hover:bg-white/[0.08] hover:text-white"
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          {/* 分类是真实搜索范围而非结果筛选：点击后更新 URL，并按该配置重新请求站点。 */}
          {vertical === "torrent" && (
            <div
              role="radiogroup"
              aria-label="站点资源搜索分类"
              className="flex flex-wrap items-center gap-1"
            >
              <ScopeChip
                label="全部"
                active={scopeEquals(query.scope, SCOPE_ALL)}
                onClick={() => onScopeSwitch(SCOPE_ALL)}
              />
              {visibleTabs.map((tab) => {
                const scope = scopeOfTab(tab);
                return (
                  <ScopeChip
                    key={tabKeyOf(tab)}
                    label={tab.type === "category" ? CATEGORY_LABEL[tab.id] : tab.name}
                    active={scopeEquals(query.scope, scope)}
                    onClick={() => onScopeSwitch(scope)}
                  />
                );
              })}
            </div>
          )}
        </div>
      </div>

      {visited.media && (
        <div className={vertical === "media" ? "min-h-0 flex-1" : "hidden"}>
          <MediaSearchResults
            keyword={query.keyword}
            snapshotId={query.snapshotId}
            // 媒体快照的「重新搜索」= 留在媒体垂直、丢掉 snapshot 参数：
            // onSwitch 本身会剥离 snapshot，目标还是 media 时即为原地重搜
            onResearch={() => onSwitch("media")}
            onSwitchToTorrent={() => onSwitch("torrent")}
          />
        </div>
      )}
      {visited.torrent && (
        <div className={vertical === "torrent" ? "min-h-0 flex-1" : "hidden"}>
          <SearchResults query={query} onResearch={onResearch} />
        </div>
      )}
    </div>
  );
}

/** 内置分类与自定义预设的稳定 UI key。 */
function tabKeyOf(tab: SearchTab): string {
  return `${tab.type}:${tab.id}`;
}

/**
 * 判断 URL 还原出的当前范围是否对应某个分类。
 * 数组来自同一份分类配置，顺序有意义且会被 URL 原样保留，因此逐项比较即可。
 */
function scopeEquals(left: SearchScope, right: SearchScope): boolean {
  return (
    left.label === right.label &&
    left.posterMode === right.posterMode &&
    left.skipHistory === right.skipHistory &&
    left.categories.length === right.categories.length &&
    left.categories.every((value, index) => value === right.categories[index]) &&
    left.siteIds.length === right.siteIds.length &&
    left.siteIds.every((value, index) => value === right.siteIds[index])
  );
}

/** 站点资源分类 chip；视觉与全局搜索弹窗里的分类选择保持一致。 */
function ScopeChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      onClick={() => !active && onClick()}
      className={`rounded-full px-2.5 py-1 text-[12px] transition-colors ${
        active
          ? "bg-white/[0.16] font-medium text-white"
          : "text-[rgba(243,245,249,0.68)] hover:bg-white/[0.08] hover:text-white"
      }`}
    >
      {label}
    </button>
  );
}
