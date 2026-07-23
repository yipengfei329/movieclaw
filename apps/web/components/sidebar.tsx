"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  BookmarkIcon,
  ClockIcon,
  LayersIcon,
  MoreIcon,
  PanelLeftIcon,
  PencilIcon,
  PlusIcon,
  TrashIcon,
} from "@/components/icons";
import { UserMenu } from "@/components/user-menu";
import { useAgentConversations } from "@/lib/agent-conversations";
import { useBackdrop } from "@/lib/backdrop";
import { sidebarGlass } from "@/lib/glass";
import { formatRelativeTime } from "@/lib/time";
import { useUiPrefs } from "@/lib/ui-prefs";
import { exploreItems } from "@/lib/mock-data";
import { GlassPanel } from "@/components/glass-panel";
import { SearchCommand, type SearchSubmitOptions } from "@/components/search-command";
import type { SearchScope } from "@/lib/categories";

/**
 * 工作台侧边栏。结构（自上而下，对齐 ChatGPT Codex 侧栏的极简版式）：
 *   品牌头部（右侧搜索图标，⌘K）→ 扁平主导航 → 「最近会话」分组 → 左下角用户信息。
 * 面板本体用真实 WebGL 液态玻璃（GlassPanel, dark 预设）承载，透出并折射背景大图，
 * 是整屏的视觉主角。
 *
 * 折叠形态（collapsed）：只留图标的窄玻璃条——品牌徽标 / 开关 / 搜索竖排在头部，
 * 主导航仅显示居中图标（title 提示文案），「最近会话」整组隐藏，左下角只留头像。
 * 宽度动画由外层 app-shell 的 aside 承担，本组件只负责两种版式的内容切换。
 */
export interface SidebarProps {
  activeNav: string;
  onSelect: (id: string) => void;
  onSearch: (keyword: string, scope: SearchScope, options?: SearchSubmitOptions) => void;
  onOpenSettings: (sectionId?: string) => void;
  /** 是否处于折叠（图标窄栏）形态 */
  collapsed: boolean;
  /** 点击头部开关按钮：在展开 / 折叠间切换 */
  onToggleCollapse: () => void;
  /** 实色形态：沉浸页（Agent 对话）用——不渲染 WebGL 玻璃、不折射背景大图 */
  flat?: boolean;
}

/** 主导航：新任务 / 媒体库 / 订阅 + 探索项，合并成一列扁平列表 */
const mainNavItems = [
  { id: "new", label: "新任务", icon: PlusIcon },
  { id: "library", label: "媒体库", icon: LayersIcon },
  { id: "subscriptions", label: "我的订阅", icon: BookmarkIcon },
  ...exploreItems,
];

export function Sidebar({
  activeNav,
  onSelect,
  onSearch,
  onOpenSettings,
  collapsed,
  onToggleCollapse,
  flat = false,
}: SidebarProps) {
  const { backdrop } = useBackdrop();
  const { conversations, rename, remove } = useAgentConversations();

  /** 重命名会话：沿用全站的原生弹窗风格；空输入或未改动直接放弃。 */
  const handleRename = (id: string, currentTitle: string) => {
    const input = window.prompt("重命名会话", currentTitle);
    if (input == null) return;
    const title = input.trim().slice(0, 80);
    if (!title || title === currentTitle) return;
    void rename(id, title).catch((error) => {
      window.alert(`重命名失败：${(error as Error).message}`);
    });
  };

  /** 彻底删除会话（二次确认）；删的是当前打开的会话时回到新任务页。 */
  const handleDelete = (id: string, title: string) => {
    const ok = window.confirm(
      `彻底删除会话「${title}」？\n服务器上的完整对话记录将一并删除，此操作不可恢复。`,
    );
    if (!ok) return;
    void remove(id)
      .then(() => {
        if (activeNav === id) onSelect("new");
      })
      .catch((error) => {
        window.alert(`删除失败：${(error as Error).message}`);
      });
  };
  // 透明度/明暗/厚度来自「设置 → 外观」的用户偏好（ui.preferences.sidebar），
  // 基底为 LiquidGlassCard 同款材质；设置页拖动滑杆时经预览草稿实时生效。
  const { prefs } = useUiPrefs();
  const glass = sidebarGlass(prefs.sidebar);
  const body = (
    <>
      {/* 品牌头部。展开：完整字标 + 开合/搜索图标横排；折叠：独立徽标、开合、搜索竖排居中。
          开合按钮与搜索共用同一套图标按钮样式（⌘K 在两种形态下均可唤起搜索）。 */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-2 px-3 pb-3 pt-5">
          <Image
            src="/movieclaw-logo-mark-rotor.png"
            alt="MovieClaw"
            width={525}
            height={525}
            priority
            className="size-9 object-contain"
          />
          <CollapseToggle collapsed onClick={onToggleCollapse} />
          <SearchCommand onSearch={onSearch} />
        </div>
      ) : (
        <div className="flex items-center justify-between px-4 pb-3 pt-4">
          <Image
            src="/movieclaw-logo-rotor.png"
            alt="MovieClaw"
            width={1920}
            height={525}
            priority
            className="h-9 w-auto max-w-[132px] object-contain"
          />
          <div className="flex items-center gap-1">
            <SearchCommand onSearch={onSearch} />
            <CollapseToggle collapsed={false} onClick={onToggleCollapse} />
          </div>
        </div>
      )}

      {/* 可滚动的导航区 */}
      <nav className="scroll-thin flex-1 overflow-y-auto px-3 pb-2">
        {/* 主导航：无分组标题、无图标底片的扁平列表（对齐 Codex 侧栏）；
            折叠时只留居中图标，文案降级为 title 悬浮提示 */}
        <div className="space-y-0.5">
          {mainNavItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                data-active={activeNav === item.id}
                onClick={() => onSelect(item.id)}
                title={collapsed ? item.label : undefined}
                className={`glass-row nav-item py-2 text-[13px] ${
                  collapsed ? "justify-center px-0" : "px-3"
                }`}
              >
                <Icon className="size-[18px] shrink-0" />
                {!collapsed && <span className="flex-1 font-medium">{item.label}</span>}
              </button>
            );
          })}
        </div>

        {/* 分组：最近会话（真实 Agent 会话，按最近更新排序；折叠时整组隐藏） */}
        {!collapsed && (
          <div className="mt-6">
            <Section label="最近会话" icon={<ClockIcon className="size-3.5" />}>
              <div className="space-y-0.5">
                {conversations.length === 0 ? (
                  <p className="px-2.5 py-1 text-[11px] leading-5 text-[var(--text-faint)]">
                    还没有会话，从「新任务」开始。
                  </p>
                ) : (
                  conversations.slice(0, 12).map((c) => (
                    <RunRow
                      key={c.id}
                      title={c.title}
                      running={c.running}
                      time={formatRelativeTime(new Date(c.updatedAt).toISOString())}
                      active={activeNav === c.id}
                      onClick={() => onSelect(c.id)}
                      onRename={() => handleRename(c.id, c.title)}
                      onDelete={() => handleDelete(c.id, c.title)}
                    />
                  ))
                )}
              </div>
            </Section>
          </div>
        )}
      </nav>

      {/* 左下角：用户信息（无分割线，靠间距区隔）；折叠时只留头像 */}
      <div className="p-2.5 pt-1.5">
        <UserMenu onOpenSettings={onOpenSettings} collapsed={collapsed} />
      </div>
    </>
  );

  if (flat) {
    // 沉浸页的实色侧栏：只保留浮起卡片的形状语言，不透玻璃
    return <div className="panel--sidebar-flat flex h-full flex-col">{body}</div>;
  }
  return (
    <GlassPanel
      backgroundImage={backdrop}
      variant={glass.variant}
      className="panel--sidebar h-full"
      contentClassName="flex h-full flex-col"
      sampleBackground={glass.sampleBackground}
      settings={glass.settings}
      hairlineAlpha={glass.hairlineAlpha}
      fallbackAlpha={glass.fallbackAlpha}
    >
      {body}
    </GlassPanel>
  );
}

/** 头部的侧栏开合按钮：与搜索触发器同款的方形图标按钮 */
function CollapseToggle({ collapsed, onClick }: { collapsed: boolean; onClick: () => void }) {
  const label = collapsed ? "展开侧栏" : "收起侧栏";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className="glass-row !size-8 shrink-0 justify-center !p-0"
    >
      <PanelLeftIcon className="size-[18px]" />
    </button>
  );
}

function Section({
  label,
  icon,
  children,
}: {
  label: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5 px-2.5 pb-1">
        {icon && <span className="text-[var(--text-faint)]">{icon}</span>}
        <span className="group-label">{label}</span>
      </div>
      {children}
    </div>
  );
}

/**
 * 会话行：主体是跳转按钮；行尾叠一个「更多操作」按钮（悬停或菜单打开时可见，
 * 覆盖在时间标签的位置上）。操作菜单 Portal 到 body 展示——侧栏面板有
 * overflow 裁剪，且玻璃面板的层叠上下文会压住行内弹层。
 */
function RunRow({
  title,
  running,
  time,
  active,
  onClick,
  onRename,
  onDelete,
}: {
  title: string;
  running: boolean;
  time: string;
  active: boolean;
  onClick: () => void;
  onRename: () => void;
  onDelete: () => void;
}) {
  // 菜单打开状态即定位坐标（打开瞬间按触发按钮位置计算一次）
  const [menuPos, setMenuPos] = useState<{ left: number; top: number } | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const open = menuPos != null;

  // 点击外部、按 Esc 或滚动时关闭（菜单在 body 里，需手动判定归属）
  useEffect(() => {
    if (!open) return;
    const close = () => setMenuPos(null);
    const onPointer = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target) || moreRef.current?.contains(target)) return;
      close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    document.addEventListener("scroll", close, true);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("scroll", close, true);
    };
  }, [open]);

  const pick = (action: () => void) => {
    setMenuPos(null);
    action();
  };

  return (
    <div className="group/run relative">
      <button
        type="button"
        data-active={active}
        onClick={onClick}
        title={`更新于 ${time}`}
        className="glass-row nav-item items-center gap-2.5 px-2.5 py-1"
      >
        {/* 状态点：仿 ChatGPT 的极简指示。容器始终占位（size-[7px]）以保证所有标题左对齐，
            但只有「运行中」才在其中画出小圆点 + 向外扩散的 ping 光晕；历史会话留空占位。 */}
        <span className="relative flex size-[7px] shrink-0 items-center justify-center">
          {running && (
            <>
              <span className="absolute inline-flex size-full animate-ping rounded-full bg-[#6aa7ff] opacity-70" />
              <span className="relative size-[7px] rounded-full bg-[#6aa7ff]" />
            </>
          )}
        </span>
        {/* 标题占满整行，右端渐变透明淡出（无省略号）；悬停/菜单打开时淡出区
            加宽，行尾按钮直接浮在淡出区上——文字看起来「渐隐进」按钮下方。
            用 mask 而非渐变色遮罩：侧栏底是 WebGL 玻璃，没有可匹配的实色。 */}
        <span
          className={`flex-1 overflow-hidden whitespace-nowrap text-[13px] font-medium text-[var(--text)] ${
            open
              ? "[mask-image:linear-gradient(to_right,#000_calc(100%_-_44px),transparent_calc(100%_-_12px))]"
              : "[mask-image:linear-gradient(to_right,#000_calc(100%_-_16px),transparent)] group-hover/run:[mask-image:linear-gradient(to_right,#000_calc(100%_-_44px),transparent_calc(100%_-_12px))]"
          }`}
        >
          {title}
        </span>
      </button>

      <button
        ref={moreRef}
        type="button"
        aria-label="会话操作"
        data-active={open}
        onClick={(e) => {
          if (open) {
            setMenuPos(null);
            return;
          }
          const rect = e.currentTarget.getBoundingClientRect();
          setMenuPos({ left: rect.right - 144, top: rect.bottom + 6 });
        }}
        className={`glass-row !absolute right-1.5 top-1/2 !size-6 -translate-y-1/2 justify-center !rounded-md !p-0 transition-opacity duration-200 ${
          open ? "opacity-100" : "opacity-0 group-hover/run:opacity-100"
        }`}
      >
        <MoreIcon className="size-4" />
      </button>

      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="surface-raised w-36 overflow-hidden rounded-xl p-1.5"
            style={{ position: "fixed", left: menuPos.left, top: menuPos.top, zIndex: 50 }}
          >
            <button
              type="button"
              onClick={() => pick(onRename)}
              className="glass-row px-2.5 py-2 text-[13px] font-medium"
            >
              <PencilIcon className="size-4 shrink-0 opacity-80" />
              <span className="flex-1">重命名</span>
            </button>
            <button
              type="button"
              onClick={() => pick(onDelete)}
              className="glass-row px-2.5 py-2 text-[13px] font-medium !text-[var(--danger)] hover:!bg-[rgba(255,107,107,0.12)]"
            >
              <TrashIcon className="size-4 shrink-0 opacity-80" />
              <span className="flex-1">删除会话</span>
            </button>
          </div>,
          document.body,
        )}
    </div>
  );
}
