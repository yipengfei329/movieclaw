import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { MediaDetailView } from "@/components/media-detail-view";

/** 兜底标题；片名要等接口返回，就绪后由视图内的 usePageTitle 覆盖为「{片名}」。 */
export const metadata: Metadata = { title: "影片详情" };

/** 影片详情（/media/movie|tv/[id]）：词条信息 + 剧照 + 相似推荐。 */
export default async function MediaDetailPage({
  params,
}: {
  params: Promise<{ type: string; id: string }>;
}) {
  const { type, id } = await params;
  if (type !== "movie" && type !== "tv") notFound();
  // key 按影片切换强制重建：在详情页内点「相似推荐」跳详情时回到顶部、重拉数据
  return <MediaDetailView key={`tmdb:${type}:${id}`} type={type} id={id} source="tmdb" />;
}
