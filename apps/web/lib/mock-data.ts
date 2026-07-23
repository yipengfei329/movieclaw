/**
 * 骨架阶段的占位数据。
 * 后续接入 FastAPI 后端时，这里的静态数组会替换为接口返回（见 lib/api/）。
 * 现在只为把布局与交互跑通，字段尽量贴近真实产品形态。
 */
import type { ComponentType, SVGProps } from "react";
import {
  DownloadIcon,
  FilmIcon,
  FolderIcon,
  PaletteIcon,
  PuzzleIcon,
  SearchIcon,
  ServerIcon,
  SparkIcon,
  TerminalIcon,
  TvIcon,
  UserIcon,
} from "@/components/icons";

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

/** 「探索」分组里的操作按钮 */
export interface ExploreItem {
  id: string;
  label: string;
  icon: Icon;
}

export const exploreItems: ExploreItem[] = [
  { id: "explore-movies", label: "发现电影", icon: FilmIcon },
  { id: "explore-tv", label: "发现剧集", icon: TvIcon },
];

/** 最近会话（类 Codex / ChatGPT 的会话列表） */
export type RunStatus = "running" | "done" | "failed";

export interface RecentRun {
  id: string;
  title: string;
  preview: string;
  status: RunStatus;
  time: string;
}

export const recentRuns: RecentRun[] = [
  {
    id: "run-1",
    title: "追踪《沙丘 2》4K 资源",
    preview: "已在 3 个站点命中，正在校验做种健康度…",
    status: "running",
    time: "刚刚",
  },
  {
    id: "run-2",
    title: "订阅《幕府将军》全季",
    preview: "第 10 集已入库，等待字幕匹配。",
    status: "done",
    time: "12 分钟前",
  },
  {
    id: "run-3",
    title: "补齐《绝命毒师》缺失剧集",
    preview: "S02E07 未找到合适资源。",
    status: "failed",
    time: "1 小时前",
  },
  {
    id: "run-4",
    title: "每周热门电影自动巡检",
    preview: "已生成 8 条候选，全部确认入库。",
    status: "done",
    time: "昨天",
  },
  {
    id: "run-5",
    title: "订阅《怪奇物语》最终季",
    preview: "全季 8 集已入库，字幕匹配完成。",
    status: "done",
    time: "2 天前",
  },
  {
    id: "run-6",
    title: "清理低做种历史种子",
    preview: "已归档 23 个种子，释放 180GB。",
    status: "done",
    time: "3 天前",
  },
];

export const runStatusMeta: Record<RunStatus, { label: string; color: string }> = {
  running: { label: "运行中", color: "#6aa7ff" },
  done: { label: "已完成", color: "#4ade80" },
  failed: { label: "失败", color: "#ff6b6b" },
};

/** 设置页的分区（进入设置后替换左侧菜单） */
export interface SettingsSection {
  id: string;
  label: string;
  description: string;
  icon: Icon;
}

/** 设置分区的分组：侧栏按组渲染小节标题，组内顺序即展示顺序 */
export interface SettingsSectionGroup {
  label: string;
  items: SettingsSection[];
}

export const settingsSectionGroups: SettingsSectionGroup[] = [
  {
    label: "通用",
    items: [
      { id: "profile", label: "个人信息", description: "头像、昵称与登录密码", icon: UserIcon },
      { id: "appearance", label: "外观", description: "首页背景与界面质感", icon: PaletteIcon },
      { id: "search", label: "搜索", description: "分类栏排序与自定义分类", icon: SearchIcon },
    ],
  },
  {
    // 组内按资源接入链路排序：站点 → 插件同步 Cookie → 下载器 → 监听入库
    label: "资源与下载",
    items: [
      { id: "sites", label: "资源站点", description: "PT 站点接入与鉴权", icon: ServerIcon },
      { id: "extension", label: "浏览器插件", description: "插件安装、Cookie 同步与活动记录", icon: PuzzleIcon },
      { id: "downloaders", label: "下载器", description: "qBittorrent / Transmission 接入", icon: DownloadIcon },
      { id: "import-watch", label: "监听导入", description: "监听下载目录，完成后自动整理入库", icon: FolderIcon },
    ],
  },
  {
    label: "智能",
    items: [
      { id: "llm", label: "AI 模型", description: "大语言模型供应商接入", icon: SparkIcon },
    ],
  },
  {
    label: "系统",
    items: [
      { id: "logs", label: "系统日志", description: "后端运行日志，按天存档", icon: TerminalIcon },
    ],
  },
];

/** 扁平分区列表：路由校验、默认分区等按 id 消费的场景继续用它 */
export const settingsSections: SettingsSection[] = settingsSectionGroups.flatMap(
  (group) => group.items,
);
