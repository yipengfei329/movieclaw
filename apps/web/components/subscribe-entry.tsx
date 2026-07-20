"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { PosterVisualItem } from "@/components/poster-card";
import { SubscribeDialog, type SubscribeTarget } from "@/components/subscribe-dialog";
import { fetchDoubanMediaDetail } from "@/lib/api/discover";

/**
 * 海报卡片「订阅影片」按钮的全站入口。
 *
 * 海报卡片散落在搜索影视 / 发现电影 / 发现剧集 / Top250 / 订阅页等多层组件里，
 * 订阅弹层（SubscribeDialog）没必要每页各挂一份——这里在应用外壳挂唯一一份，
 * 卡片通过 useSubscribeEntry().open(item) 触发，与 useMediaDetail 的模式一致。
 *
 * kind（电影/剧集）是订阅预检的必填参数：发现页与 TMDB 搜索结果的卡片自带
 * type；豆瓣轻量搜索结果没有 type，点击时补拉一次豆瓣详情由后端识别类型
 * （详情有服务端缓存，代价很小），拉取失败按电影兜底——预检若因此收敛不到
 * 条目，弹层内会展示明确的错误信息，不会静默错订。
 */
interface SubscribeEntryValue {
  /** 打开订阅弹层；豆瓣来源缺 type 时先补拉详情识别类型，故为异步 */
  open: (item: PosterVisualItem) => Promise<void>;
}

const SubscribeEntryContext = createContext<SubscribeEntryValue | null>(null);

export function useSubscribeEntry(): SubscribeEntryValue {
  const value = useContext(SubscribeEntryContext);
  if (!value) {
    throw new Error("useSubscribeEntry 必须在 SubscribeEntryProvider 内使用（见 app-shell.tsx）");
  }
  return value;
}

export function SubscribeEntryProvider({ children }: { children: ReactNode }) {
  const [target, setTarget] = useState<SubscribeTarget | null>(null);

  const open = useCallback(async (item: PosterVisualItem) => {
    const source = item.source ?? "tmdb";
    let kind = item.type;
    if (!kind && source === "douban") {
      kind = await fetchDoubanMediaDetail(item.id)
        .then((detail) => detail.item.type)
        .catch(() => undefined);
    }
    setTarget({
      kind: kind ?? "movie",
      source,
      tmdbId: source === "tmdb" ? Number(item.id) : undefined,
      doubanId: source === "douban" ? item.id : undefined,
      title: item.title,
      year: item.year,
    });
  }, []);

  const value = useMemo(() => ({ open }), [open]);

  return (
    <SubscribeEntryContext.Provider value={value}>
      {children}
      <SubscribeDialog target={target} onClose={() => setTarget(null)} />
    </SubscribeEntryContext.Provider>
  );
}
