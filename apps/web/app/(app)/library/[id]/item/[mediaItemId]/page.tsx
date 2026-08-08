import type { Metadata } from "next";

import { LibraryItemDetailView } from "@/components/library-item-detail-view";

/** 兜底标题；影片名要等接口返回，就绪后由视图内的 usePageTitle 覆盖。 */
export const metadata: Metadata = { title: "影片详情" };

/**
 * 媒体库条目详情页（/library/[id]/item/[mediaItemId]）：
 * 展示"我拥有的这份拷贝"——本地刮削元数据 + 文件本体的真实介质规格，
 * 与发现页详情（/media/...，纯 TMDB 实时数据）是两个不同的页面。
 */
export default async function LibraryItemDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string; mediaItemId: string }>;
  searchParams: Promise<{ returnTo?: string | string[] }>;
}) {
  const { id, mediaItemId } = await params;
  const query = await searchParams;
  // 重复参数不产生歧义的返回目标，按缺失参数处理并回退到媒体库父级。
  const returnTo = typeof query.returnTo === "string" ? query.returnTo : undefined;
  return (
    <div className="flex h-full flex-col">
      <LibraryItemDetailView
        libraryId={Number(id)}
        mediaItemId={Number(mediaItemId)}
        returnTo={returnTo}
      />
    </div>
  );
}
