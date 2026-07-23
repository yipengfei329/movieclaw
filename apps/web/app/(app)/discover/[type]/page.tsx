import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { DiscoverView } from "@/components/discover-view";
import type { MediaSource } from "@/lib/media-types";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ type: string }>;
}): Promise<Metadata> {
  const { type } = await params;
  return { title: type === "tv" ? "发现剧集" : "发现电影" };
}

/** 发现页（/discover/movie | /discover/tv）：Hero 精选 + 分类横滚行。 */
export default async function DiscoverPage({
  params,
  searchParams,
}: {
  params: Promise<{ type: string }>;
  searchParams: Promise<{ source?: string | string[] }>;
}) {
  const { type } = await params;
  const query = await searchParams;
  if (type !== "movie" && type !== "tv") notFound();
  // URL 是发现视角的唯一状态源；未知值安全回退到默认 TMDB 视角。
  const source: MediaSource = query.source === "douban" ? "douban" : "tmdb";
  return (
    <div className="flex h-full flex-col">
      <DiscoverView key={`${type}:${source}`} mediaType={type} source={source} />
    </div>
  );
}
