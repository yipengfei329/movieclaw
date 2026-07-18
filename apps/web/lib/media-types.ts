/**
 * 影视数据的前端核心类型（发现页 / 详情页 / 订阅页共用）。
 *
 * 这些形态即组件的渲染契约：后端 /discover 接口返回 snake_case 字段，
 * 由 lib/api/discover.ts 映射成这里的 camelCase；lib/mock-media.ts 的
 * 遗留模拟数据（订阅页仍在用）也遵循同一套类型。
 */

export type MediaType = "movie" | "tv";
export type MediaSource = "tmdb" | "douban";

export interface MediaItem {
  /** TMDB 条目 ID（字符串形态，仅作不透明键使用） */
  id: string;
  /** 来源与 id 共同构成媒体条目的稳定身份 */
  source?: MediaSource;
  type: MediaType;
  /** 中文标题 */
  title: string;
  /** 原名（拉丁/原语言） */
  originalTitle: string;
  year: number;
  /** 评分（0~10，一位小数）；0 表示暂无评分 */
  rating: number;
  /** 类型标签，如「科幻 / 冒险」 */
  genres: string[];
  /** 规模：电影为时长，剧集为季数；列表数据为空，进详情后回填 */
  extent: string;
  /** 站点资源质量徽章（清晰度 / HDR / 字幕）；预留给资源匹配，当前为空 */
  badges: string[];
  /** 一句话简介（卡片 hover 与 Hero 横幅展示） */
  overview: string;
  posterUrl: string;
  /** 仅 Hero 精选项需要的宽幅背景图 */
  backdropUrl?: string;
}

/** 一行横滚海报的分类数据 */
export interface MediaRowData {
  id: string;
  title: string;
  /** Netflix 式大数字排名行（Top 10） */
  ranked?: boolean;
  items: MediaItem[];
}

/** 单个发现页（电影 / 剧集）的全部数据 */
export interface DiscoverPageData {
  /** Hero 大横幅轮播的精选项（均带 backdropUrl） */
  hero: MediaItem[];
  rows: MediaRowData[];
}
