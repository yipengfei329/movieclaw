"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { fetchSearchPreferences, updateSearchPreferences } from "@/lib/api/search";
import { DEFAULT_SEARCH_TABS, type SearchTab } from "@/lib/categories";

/**
 * 搜索偏好（标签栏：内置分类 + 自定义分类，统一混排）的全局状态。
 *
 * 偏好有两处消费者，必须始终是同一份数据、改动即时联动：
 *   1) 搜索命令面板（search-command.tsx）：按偏好渲染分类栏标签；
 *   2) 设置页「搜索」分区（search-settings.tsx）：排序/显隐/预设增删改。
 * 因此与 backdrop 同款做法：一个 React Context 作为唯一数据源。
 *
 * 数据存**服务端**（search.preferences 配置域），跨设备/浏览器一次保存
 * 处处生效；启动时拉取一次，之后每次保存都以后端返回的规范化列表回写。
 * 拉取失败（后端未起/网络问题）不致命，回退到内置默认（与后端默认一致）。
 */
interface SearchPrefsContextValue {
  /** 全量标签的有序混排列表（含隐藏项），供设置页完整渲染 */
  tabs: SearchTab[];
  /** 可见标签（按偏好顺序），供搜索面板渲染分类栏 */
  visibleTabs: SearchTab[];
  /** 首次向后端拉取是否进行中 */
  loading: boolean;
  /** 整体保存偏好；失败时状态回滚并抛错，由调用方展示错误 */
  saveTabs: (next: SearchTab[]) => Promise<void>;
}

const SearchPrefsContext = createContext<SearchPrefsContextValue | null>(null);

export function SearchPrefsProvider({ children }: { children: React.ReactNode }) {
  const [tabs, setTabs] = useState<SearchTab[]>(DEFAULT_SEARCH_TABS);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchSearchPreferences()
      .then((list) => !cancelled && setTabs(list))
      .catch((err) => {
        // 拉取失败不致命：静默沿用内置默认标签（与后端默认一致）
        console.warn("读取搜索设置失败，暂用默认分类：", err);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const saveTabs = useCallback(
    async (next: SearchTab[]) => {
      const previous = tabs;
      // 乐观更新：设置页里开关/排序/编辑立即生效；保存失败回滚并抛给调用方提示
      setTabs(next);
      try {
        setTabs(await updateSearchPreferences(next));
      } catch (err) {
        setTabs(previous);
        throw err;
      }
    },
    [tabs],
  );

  const value = useMemo<SearchPrefsContextValue>(
    () => ({
      tabs,
      visibleTabs: tabs.filter((t) => t.visible),
      loading,
      saveTabs,
    }),
    [tabs, loading, saveTabs],
  );

  return <SearchPrefsContext.Provider value={value}>{children}</SearchPrefsContext.Provider>;
}

/** 读取搜索偏好与保存方法。必须在 SearchPrefsProvider 内使用。 */
export function useSearchPrefs(): SearchPrefsContextValue {
  const ctx = useContext(SearchPrefsContext);
  if (!ctx) throw new Error("useSearchPrefs 必须在 <SearchPrefsProvider> 内使用");
  return ctx;
}
