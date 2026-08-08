import { request } from "@/lib/http";
import { cachedImageUrl } from "@/lib/image-proxy";
import type {
  DiscoverLayoutData,
  MediaLibraryLink,
  MediaLibraryStatus,
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
  library_status?: MediaLibraryStatusDto | null;
}

interface MediaLibraryStatusDto {
  media_item_id: number;
  library_count: number;
  file_count: number;
}

interface MediaLibraryLinkDto {
  library_id: number;
  library_name: string;
  media_item_id: number;
}

interface MediaRowDto {
  id: string;
  title: string;
  items: MediaCardDto[];
}

interface DiscoverLayoutDto {
  has_hero: boolean;
  rows: { id: string; title: string }[];
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

interface MediaCastMemberDto {
  name: string;
  role: string | null;
  avatar_url: string | null;
  tmdb_person_id: number | null;
}

interface MediaFactsDto {
  directors: string[];
  cast: MediaCastMemberDto[];
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
  library_links?: MediaLibraryLinkDto[];
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
    libraryStatus: toLibraryStatus(dto.library_status),
  };
}

/** 库存字段只在 API 边界完成 snake_case 到 camelCase 的转换。 */
function toLibraryStatus(dto: MediaLibraryStatusDto | null | undefined): MediaLibraryStatus | null {
  if (!dto) return null;
  return {
    mediaItemId: dto.media_item_id,
    libraryCount: dto.library_count,
    fileCount: dto.file_count,
  };
}

function toLibraryLink(dto: MediaLibraryLinkDto): MediaLibraryLink {
  return {
    libraryId: dto.library_id,
    libraryName: dto.library_name,
    mediaItemId: dto.media_item_id,
  };
}

function toRow(dto: MediaRowDto): MediaRowData {
  return { id: dto.id, title: dto.title, items: dto.items.map(toItem) };
}

// ---------------------------------------------------------------------------
// 发现页 / 条目详情
// ---------------------------------------------------------------------------

/**
 * 拉取发现页布局（行清单 + Hero 有无）。纯配置毫秒级返回；
 * 前端据此撑起整页骨架，再用 fetchDiscoverHero / fetchDiscoverRow 逐行填充。
 */
export async function fetchDiscoverLayout(
  type: MediaType,
  source: MediaSource = "tmdb",
  init?: RequestInit,
): Promise<DiscoverLayoutData> {
  const dto = await unwrap(
    request<ApiEnvelope<DiscoverLayoutDto>>(`/discover/${type}/layout?source=${source}`, init),
  );
  return { hasHero: dto.has_hero, rows: dto.rows };
}

/** 拉取 Hero 大横幅精选；无 Hero 的数据源（豆瓣）返回空数组。 */
export async function fetchDiscoverHero(
  type: MediaType,
  source: MediaSource = "tmdb",
  init?: RequestInit,
): Promise<MediaItem[]> {
  const dto = await unwrap(
    request<ApiEnvelope<MediaCardDto[]>>(`/discover/${type}/hero?source=${source}`, init),
  );
  return dto.map(toItem);
}

/** 拉取发现页的一行数据；条目太少的行后端返回空 items，由调用方收起。 */
export async function fetchDiscoverRow(
  type: MediaType,
  rowId: string,
  source: MediaSource = "tmdb",
  init?: RequestInit,
): Promise<MediaRowData> {
  const dto = await unwrap(
    request<ApiEnvelope<MediaRowDto>>(
      `/discover/${type}/rows/${encodeURIComponent(rowId)}?source=${source}`,
      init,
    ),
  );
  return toRow(dto);
}

/**
 * 拉取一份「看全部」落地页的完整豆瓣榜单（如 Top 250、豆瓣高分电影）。
 * 后端按白名单聚合分页并缓存；冷缓存时受豆瓣限速影响可能需要数秒。
 */
export async function fetchDoubanCollection(
  collectionId: string,
  init?: RequestInit,
): Promise<MediaRowData> {
  const dto = await unwrap(
    request<ApiEnvelope<MediaRowDto>>(`/discover/douban/collection/${collectionId}`, init),
  );
  return toRow(dto);
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

/** 演职员条的一行：姓名 + 饰演角色 + 头像（数据源缺哪项就是空，前端按占位渲染）。 */
export interface MediaCastMember {
  name: string;
  role?: string;
  avatarUrl?: string;
  /** TMDB 影人 ID：有值时详情页把这一格链到人物页；豆瓣来源没有此 id */
  tmdbPersonId?: number;
}

/** 详情页「词条信息」卡的字段（导演 / 演职员 / 地区 / 语言 / 日期 / 平台）。 */
export interface MediaDetailInfo {
  directors: string[];
  /** 演职员（按数据源给出的主次顺序），详情页用横滚条呈现而不是塞进词条信息 */
  cast: MediaCastMember[];
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
  /** 已入库时按后端稳定顺序返回的媒体库详情入口。 */
  libraryLinks: MediaLibraryLink[];
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
      cast: dto.facts.cast.map((c) => ({
        name: c.name,
        role: c.role ?? undefined,
        // 头像走图片代理：豆瓣图床按 Referer 防盗链，直连会 403
        avatarUrl: c.avatar_url ? cachedImageUrl(c.avatar_url) : undefined,
        tmdbPersonId: c.tmdb_person_id ?? undefined,
      })),
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
    libraryLinks: (dto.library_links ?? []).map(toLibraryLink),
  };
}
