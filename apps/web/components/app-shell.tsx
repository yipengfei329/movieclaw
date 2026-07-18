"use client";

import { useEffect, useState } from "react";
import type { Route } from "next";
import { usePathname, useRouter } from "next/navigation";

/** 侧栏折叠态的本地持久化 key（设备级偏好，不进后端 ui.preferences） */
const SIDEBAR_COLLAPSED_KEY = "movieclaw.sidebar-collapsed";
/** 进设置前所在的工作台地址（含查询串），设置页「返回工作台」按原样回跳 */
const SETTINGS_RETURN_KEY = "movieclaw.settings-return";

import { type SearchSubmitOptions } from "@/components/search-command";
import { SettingsSidebar } from "@/components/settings-view";
import { Sidebar } from "@/components/sidebar";
import { AgentConversationsProvider } from "@/lib/agent-conversations";
import { BackdropProvider } from "@/lib/backdrop";
import type { SearchScope } from "@/lib/categories";
import { SearchPrefsProvider } from "@/lib/search-prefs";
import { buildSearchPath } from "@/lib/search-url";
import { UiPrefsProvider } from "@/lib/ui-prefs";
import { settingsSections } from "@/lib/mock-data";

/**
 * 应用外壳：全站骨架布局（左栏 + 右区），所有导航态由 URL 驱动。
 *
 * 每个页面都是真实路由——刷新保留、可分享、前进后退可用：
 *   /                    新任务（氛围首页）
 *   /discover/movie|tv   发现电影 / 剧集
 *   /subscriptions       我的订阅
 *   /media/[type]/[id]   影片详情
 *   /search?q=…          跨站搜索结果（范围/快照都在查询参数里）
 *   /runs/[id]           任务会话
 *   /settings/[section]  设置各分区
 *
 * 布局参考 Codex / Claude Code：两种模式共用「左栏 + 右区」两栏结构：
 *   - workspace（工作台）：左 = 常规侧边栏，右 = 当前路由页面
 *   - settings（设置）  ：左 = 设置分区菜单（含返回按钮），右 = 分区内容
 * 设置不是弹窗，而是整体替换左栏内容；模式由 pathname 是否在 /settings 下推导。
 */

/** pathname → 侧栏选中项 id（找不到对应项时返回空串，侧栏无高亮） */
function navIdFromPath(pathname: string): string {
  if (pathname === "/") return "new";
  if (pathname.startsWith("/subscriptions")) return "subscriptions";
  if (pathname.startsWith("/discover/movie")) return "explore-movies";
  if (pathname.startsWith("/discover/tv")) return "explore-tv";
  const run = /^\/runs\/([^/]+)$/.exec(pathname);
  return run ? run[1] : "";
}

/** 侧栏项 id → 路由地址（与 navIdFromPath 互逆） */
function pathOfNavId(id: string): Route {
  switch (id) {
    case "new":
      return "/";
    case "subscriptions":
      return "/subscriptions";
    case "explore-movies":
      return "/discover/movie";
    case "explore-tv":
      return "/discover/tv";
    default:
      return `/runs/${id}` as Route;
  }
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();

  const isSettings = pathname.startsWith("/settings");
  const activeNav = navIdFromPath(pathname);
  // 设置分区从路径推导：/settings/appearance → appearance；/settings 兜底到首个分区
  const activeSettings = isSettings
    ? (pathname.split("/")[2] ?? settingsSections[0].id)
    : settingsSections[0].id;

  // 侧栏折叠态：收起后只留图标窄栏，主区铺开、更沉浸。存 localStorage 记住设备偏好；
  // AuthGate 确认登录后才在客户端渲染本组件，故初始化时可直接读取、无 SSR 水合问题。
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch {
      return false; // localStorage 不可用（隐私模式等）时仅失去记忆，功能不受影响
    }
  });

  const toggleSidebar = () => {
    setSidebarCollapsed((v) => {
      const next = !v;
      try {
        localStorage.setItem(SIDEBAR_COLLAPSED_KEY, next ? "1" : "0");
      } catch {
        // 忽略写入失败
      }
      return next;
    });
  };

  /** 选中侧栏导航项：跳对应路由（离开搜索结果/详情页由路由切换自然完成）。 */
  const handleSelect = (id: string) => {
    router.push(pathOfNavId(id));
  };

  /**
   * 提交搜索：关键词 + 范围（标签换算而来）编码进 /search 的查询参数。
   * options.vertical 决定落地垂直（媒体/站点资源，见 URL 的 tab 参数）；
   * options.snapshotId 非空 = 预览某条历史的结果快照（结果页顶部有提示条与「重新搜索」）。
   */
  const handleSearch = (keyword: string, scope: SearchScope, options?: SearchSubmitOptions) => {
    router.push(
      buildSearchPath(
        { keyword, scope, snapshotId: options?.snapshotId },
        options?.vertical,
      ) as Route,
    );
  };

  /** 从用户菜单进入设置：记下当前工作台地址（含查询串），返回时原样回跳 */
  const openSettings = (sectionId: string = settingsSections[0].id) => {
    try {
      sessionStorage.setItem(
        SETTINGS_RETURN_KEY,
        window.location.pathname + window.location.search,
      );
    } catch {
      // 记不住就退回首页，不影响进入设置
    }
    router.push(`/settings/${sectionId}` as Route);
  };

  const backToWorkspace = () => {
    let target = "/";
    try {
      target = sessionStorage.getItem(SETTINGS_RETURN_KEY) || "/";
    } catch {
      // 读取失败回首页
    }
    router.push(target as Route);
  };

  // 全站布局规范：默认所有页面都铺一层模糊蒙版（.page-scrim）压住背景大图、
  // 突出页面主题内容；唯一例外是「新任务」首页（路由 /）——保持大图直出，
  // 作为全站的「氛围页」门面。新增路由无需登记，自动继承蒙版。
  const isHome = pathname === "/";
  // Agent 对话页走沉浸模式：蒙版换成完全不透明的 .page-solid，整页盖掉
  // 背景大图（密集文本页不允许透图）；侧栏切换为实色形态。
  const isImmersive = pathname.startsWith("/runs");

  // 沉浸路由标记：强刷时由 layout.tsx 的内联脚本在首帧绘制前打上（避免闪出
  // 背景大图），这里负责客户端路由切换时的双向同步——进入 /runs 关掉背景
  // 大图伪元素，离开时恢复其他页面的大图。
  useEffect(() => {
    document.documentElement.classList.toggle("immersive-route", isImmersive);
  }, [isImmersive]);

  return (
    // BackdropProvider 提供全站唯一的背景图数据源（CSS 大图 + 玻璃折射纹理），
    // 让「外观」设置里上传的图能同步作用到 body::before 与所有玻璃面板。
    <BackdropProvider>
    {/* SearchPrefsProvider / UiPrefsProvider：搜索偏好与界面样式的全站唯一数据源，
      应用启动各拉取一次、Context 共享，设置页的改动即时同步到所有消费页面。 */}
    <SearchPrefsProvider>
    <UiPrefsProvider>
    {/* AgentConversationsProvider：Agent 会话的全站状态（侧栏最近会话 +
      /runs/[id] 会话页共用），本地持久化运行编号并在刷新后自动回放未完成任务。 */}
    <AgentConversationsProvider>
    {/* 全屏背景蒙版（.page-scrim）：作为 .app-shell 的兄弟节点、
      z 介于 body::before(0) 与 app-shell(10) 之间：压住背景大图、托住内容。
      全站统一一档，模糊度/暗度由「设置 → 外观 → 界面质感」的滑杆驱动
      （--scrim-blur / --scrim-dark，见 lib/ui-prefs.tsx）；唯一例外是
      「新任务」首页——大图直出的氛围页门面，不铺蒙版（见 isHome）。 */}
    {!isHome && (
      <div className={isImmersive ? "page-solid" : "page-scrim"} aria-hidden="true" />
    )}
    {/* 浮起圆角卡片布局（对齐参考站 liquid-glass-oss）：外层留 padding、两栏留间隙，
      背景大图在卡片四周与中缝透出，面板作为浮于图上的玻璃卡片。
      app-shell 类名是给命令面板的「主界面后推」纵深效果用的锚点（见 globals.css 的
      body.cmdk-open .app-shell）：面板打开时整个外壳轻微缩放后退，浮层则漂在其上。 */}
    <div className="app-shell relative z-10 flex h-screen w-screen gap-3.5 p-3.5">
      {/* —— 左栏：浮起的玻璃侧栏卡片 ——
        宽度随折叠态动画（仅工作台可折叠；设置模式的分区菜单始终全宽）。 */}
      <aside
        className={`h-full shrink-0 transition-[width] duration-300 ease-[cubic-bezier(0.2,0.8,0.2,1)] ${
          !isSettings && sidebarCollapsed ? "w-[68px]" : "w-[300px]"
        }`}
      >
        {isSettings ? (
          <SettingsSidebar
            active={activeSettings}
            onSelect={(id) => router.push(`/settings/${id}` as Route)}
            onBack={backToWorkspace}
          />
        ) : (
          <Sidebar
            activeNav={activeNav}
            onSelect={handleSelect}
            onSearch={handleSearch}
            onOpenSettings={openSettings}
            collapsed={sidebarCollapsed}
            onToggleCollapse={toggleSidebar}
            flat={isImmersive}
          />
        )}
      </aside>

      {/* —— 右区：当前路由页面 —— */}
      <main className="h-full min-w-0 flex-1">{children}</main>
    </div>
    </AgentConversationsProvider>
    </UiPrefsProvider>
    </SearchPrefsProvider>
    </BackdropProvider>
  );
}
