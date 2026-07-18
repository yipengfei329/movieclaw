import { request } from "@/lib/http";
import { cachedImageUrl } from "@/lib/image-proxy";
import type {
  DiscoverPageData,
  MediaItem,
  MediaRowData,
  MediaType,
  MediaSource,
} from "@/lib/media-types";

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

// ---------------------------------------------------------------------------
// 后端 DTO（snake_case，见 movieclaw_media.models）→ 前端类型的映射
// ---------------------------------------------------------------------------

interface MediaCardDto {
  id: string;
  source: MediaSource;
  type: MediaType;
  title: string;
  original_title: string;
  year: number;
  rating: number;
  genres: string[];
  extent: string;
  badges: string[];
  overview: string;
  poster_url: string;
  backdrop_url: string | null;
}

interface MediaRowDto {
  id: string;
  title: string;
  ranked: boolean;
  items: MediaCardDto[];
}

interface DiscoverPageDto {
  hero: MediaCardDto[];
  rows: MediaRowDto[];
}

interface MediaSearchItemDto {
  id: string;
  source: "douban" | "tmdb";
  title: string;
  /** 豆瓣轻量搜索不提供年份/类型（旧快照也没有这两个字段），仅 TMDB 来源有值 */
  year?: number | null;
  type?: "movie" | "tv" | null;
  rating: number;
  poster_url: string;
}

export interface MediaSearchItem {
  id: string;
  source: "douban" | "tmdb";
  title: string;
  year?: number;
  type?: "movie" | "tv";
  rating: number;
  posterUrl: string;
}

interface MediaFactsDto {
  directors: string[];
  cast: string[];
  country: string;
  language: string;
  released: string;
  network: string | null;
  aliases: string[];
  source_url: string | null;
}

interface MediaImageDto {
  preview_url: string;
  full_url: string;
  width: number;
  height: number;
}

interface MediaDetailDto {
  card: MediaCardDto;
  facts: MediaFactsDto;
  backdrops: MediaImageDto[];
  posters: MediaImageDto[];
  related: MediaCardDto[];
}

function toItem(dto: MediaCardDto): MediaItem {
  return {
    id: dto.id,
    source: dto.source,
    type: dto.type,
    title: dto.title,
    originalTitle: dto.original_title,
    year: dto.year,
    rating: dto.rating,
    genres: dto.genres,
    extent: dto.extent,
    badges: dto.badges,
    overview: dto.overview,
    posterUrl: cachedImageUrl(dto.poster_url),
    backdropUrl: dto.backdrop_url ? cachedImageUrl(dto.backdrop_url) : undefined,
  };
}

function toRow(dto: MediaRowDto): MediaRowData {
  return { id: dto.id, title: dto.title, ranked: dto.ranked, items: dto.items.map(toItem) };
}

// ---------------------------------------------------------------------------
// 发现页 / 条目详情
// ---------------------------------------------------------------------------

/** 拉取一个完整发现页（Hero 精选 + 分类横滚行），服务端聚合 TMDB 并缓存。 */
export async function fetchDiscoverPage(
  type: MediaType,
  source: MediaSource = "tmdb",
  init?: RequestInit,
): Promise<DiscoverPageData> {
  const dto = await unwrap(
    request<ApiEnvelope<DiscoverPageDto>>(`/discover/${type}?source=${source}`, init),
  );
  return { hero: dto.hero.map(toItem), rows: dto.rows.map(toRow) };
}

/**
 * 搜索豆瓣轻量影视候选；年份和类型需要后续详情/匹配阶段补齐。
 * options.history=true 时后端记录搜索历史并留存结果快照（统一搜索入口用；
 * 发现页工具栏等场景不传，不产生历史）。
 */
export async function searchDoubanMedia(
  query: string,
  options?: { history?: boolean },
  init?: RequestInit,
): Promise<MediaSearchItem[]> {
  const history = options?.history ? "&history=true" : "";
  const items = await unwrap(
    request<ApiEnvelope<MediaSearchItemDto[]>>(
      `/discover/search?source=douban&q=${encodeURIComponent(query)}${history}`,
      init,
    ),
  );
  return items.map(toSearchItem);
}

/**
 * 搜索 TMDB 轻量影视候选（multi 搜索，电影/剧集按全局热度排序）。
 * 不记录搜索历史——搜索页对同一关键词并行搜豆瓣和 TMDB，历史只随豆瓣请求记一条。
 */
export async function searchTmdbMedia(
  query: string,
  init?: RequestInit,
): Promise<MediaSearchItem[]> {
  const items = await unwrap(
    request<ApiEnvelope<MediaSearchItemDto[]>>(
      `/discover/search?source=tmdb&q=${encodeURIComponent(query)}`,
      init,
    ),
  );
  return items.map(toSearchItem);
}

/** 轻量搜索条目 DTO → 前端视图（海报走缓存代理），搜索与快照回放共用。 */
export function toSearchItem(item: MediaSearchItemDto): MediaSearchItem {
  return {
    id: item.id,
    source: item.source,
    title: item.title,
    year: item.year ?? undefined,
    type: item.type ?? undefined,
    rating: item.rating,
    posterUrl: cachedImageUrl(item.poster_url),
  };
}

/** 详情页「词条信息」卡的字段（导演 / 主演 / 地区 / 语言 / 日期 / 平台）。 */
export interface MediaDetailInfo {
  directors: string[];
  cast: string[];
  country: string;
  language: string;
  released: string;
  network?: string;
  aliases: string[];
  sourceUrl?: string;
}

/** 一张剧照/海报：横滚条用预览图，灯箱看原图 */
export interface MediaImage {
  previewUrl: string;
  fullUrl: string;
  width: number;
  height: number;
}

export interface MediaDetailData {
  /** 详情接口回填过 extent（片长/季数）的完整卡片字段 */
  item: MediaItem;
  info: MediaDetailInfo;
  /** 剧照（16:9 宽幅） */
  backdrops: MediaImage[];
  /** 海报（2:3 竖版，中文版优先） */
  posters: MediaImage[];
  /** TMDB 推荐的相似作品 */
  related: MediaItem[];
}

function toImage(dto: MediaImageDto): MediaImage {
  return {
    previewUrl: cachedImageUrl(dto.preview_url),
    fullUrl: cachedImageUrl(dto.full_url),
    width: dto.width,
    height: dto.height,
  };
}

/** 拉取单个条目的详情：词条信息 + 相似推荐。 */
export async function fetchMediaDetail(
  type: MediaType,
  id: string,
  init?: RequestInit,
): Promise<MediaDetailData> {
  const dto = await unwrap(
    request<ApiEnvelope<MediaDetailDto>>(`/discover/${type}/${id}`, init),
  );
  return toDetail(dto);
}

/** 拉取独立豆瓣详情；电影/剧集类型由后端根据豆瓣响应识别。 */
export async function fetchDoubanMediaDetail(
  id: string,
  init?: RequestInit,
): Promise<MediaDetailData> {
  const dto = await unwrap(
    request<ApiEnvelope<MediaDetailDto>>(`/discover/douban/${id}`, init),
  );
  return toDetail(dto);
}

function toDetail(dto: MediaDetailDto): MediaDetailData {
  return {
    item: toItem(dto.card),
    info: {
      directors: dto.facts.directors,
      cast: dto.facts.cast,
      country: dto.facts.country,
      language: dto.facts.language,
      released: dto.facts.released,
      network: dto.facts.network ?? undefined,
      aliases: dto.facts.aliases,
      sourceUrl: dto.facts.source_url ?? undefined,
    },
    backdrops: dto.backdrops.map(toImage),
    posters: dto.posters.map(toImage),
    related: dto.related.map(toItem),
  };
}
