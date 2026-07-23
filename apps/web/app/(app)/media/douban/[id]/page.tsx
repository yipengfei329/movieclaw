import type { Metadata } from "next";

import { MediaDetailView } from "@/components/media-detail-view";

/** 兜底标题；片名要等接口返回，就绪后由视图内的 usePageTitle 覆盖为「{片名}」。 */
export const metadata: Metadata = { title: "影片详情" };

/** 豆瓣独立详情路由；视觉与 TMDB 详情页完全复用 MediaDetailView。 */
export default async function DoubanMediaDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <MediaDetailView key={`douban:${id}`} id={id} source="douban" />;
}
