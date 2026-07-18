/**
 * 种子搜索的一级分类。
 *
 * 值与后端 movieclaw_tracker 的 TorrentCategory 枚举**逐一对应**（movie/tv/…），
 * 搜索时作为 `category` 查询参数原样传给后端；label 仅用于前端展示。
 * 顺序即 UI 里分类标签的排列顺序，把最常用的电影/剧集放在前面。
 */
export type TorrentCategory =
  | "movie"
  | "tv"
  | "documentary"
  | "anime"
  | "music"
  | "game"
  | "av"
  | "other";

export interface CategoryOption {
  value: TorrentCategory;
  label: string;
}

export const CATEGORY_OPTIONS: CategoryOption[] = [
  { value: "movie", label: "电影" },
  { value: "tv", label: "剧集" },
  { value: "documentary", label: "纪录片" },
  { value: "anime", label: "动漫" },
  { value: "music", label: "音乐" },
  { value: "game", label: "游戏" },
  { value: "av", label: "成人" },
  { value: "other", label: "其他" },
];

/** 分类值 → 中文标签，便于在结果页给单条种子回显分类名。 */
export const CATEGORY_LABEL: Record<TorrentCategory, string> = Object.fromEntries(
  CATEGORY_OPTIONS.map((c) => [c.value, c.label]),
) as Record<TorrentCategory, string>;

/**
 * 搜索标签栏的标签（与后端 schemas.search.SearchTabItem 一致），
 * 列表顺序即搜索面板中标签的展示顺序，内置分类与自定义分类统一混排。
 */
export interface CategoryTab {
  type: "category";
  id: TorrentCategory;
  visible: boolean;
}

/** 自定义分类：命名的「分类组合 × 站点组合」预设。 */
export interface PresetTab {
  type: "preset";
  /** 创建时生成的随机短 id，历史与前端引用它 */
  id: string;
  name: string;
  visible: boolean;
  /** 勾选的一级分类；空 = 不限分类 */
  categories: TorrentCategory[];
  /** 勾选的站点；空 = 全部可用站点 */
  site_ids: string[];
  /** 图览模式：用该分类搜索时，结果页默认以图墙展示（结果页可临时切换） */
  poster_mode: boolean;
  /** 无痕搜索：用该分类搜索时不写入搜索历史（隐私敏感场景的开关） */
  skip_history: boolean;
}

export type SearchTab = CategoryTab | PresetTab;

/**
 * 搜索的垂直类别（Google 式「综合/图片」的对应物）：
 *   media   影视条目（当前只搜豆瓣，轻量元数据，毫秒级）
 *   torrent 站点资源（跨 PT 站并发搜种子，秒级，惰性触发）
 * 搜索弹窗的「搜媒体/搜资源」模式与结果页顶部选项卡共用该类型。
 */
export type SearchVertical = "media" | "torrent";

/**
 * 一次搜索的范围：选中某个标签后传给搜索接口的参数组合。
 * label 仅用于历史与结果页展示（分类中文名/预设名）；null = 「全部」。
 */
export interface SearchScope {
  label: string | null;
  categories: TorrentCategory[];
  siteIds: string[];
  /** 结果页图览模式的初始值：自定义分类取各自设定，内置分类/「全部」/历史重搜为 false */
  posterMode: boolean;
  /** 无痕搜索：为 true 时本次搜索不写入搜索历史（来自自定义分类的设定） */
  skipHistory: boolean;
}

/** 「全部」标签对应的搜索范围：不限分类 × 全部站点。 */
export const SCOPE_ALL: SearchScope = {
  label: null,
  categories: [],
  siteIds: [],
  posterMode: false,
  skipHistory: false,
};

/** 把一个标签换算成搜索范围（内置分类 = 单分类 × 全部站点）。 */
export function scopeOfTab(tab: SearchTab): SearchScope {
  if (tab.type === "category") {
    return {
      label: CATEGORY_LABEL[tab.id],
      categories: [tab.id],
      siteIds: [],
      posterMode: false,
      skipHistory: false,
    };
  }
  return {
    label: tab.name,
    categories: tab.categories,
    siteIds: tab.site_ids,
    posterMode: tab.poster_mode,
    skipHistory: tab.skip_history,
  };
}

/**
 * 默认标签列表：常用四类可见，其余（音乐/游戏/成人/其他）隐藏，无预设。
 * 与后端 settings.schemas.default_search_tabs 保持一致——
 * 后端拉取失败时前端以此兜底，行为与历史版本硬编码的标签相同。
 */
export const DEFAULT_SEARCH_TABS: SearchTab[] = (
  [
    ["movie", true],
    ["tv", true],
    ["documentary", true],
    ["anime", true],
    ["music", false],
    ["game", false],
    ["av", false],
    ["other", false],
  ] as const
).map(([id, visible]) => ({ type: "category", id, visible }));
