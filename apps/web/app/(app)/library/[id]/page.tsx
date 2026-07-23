import type { Metadata } from "next";

import { LibraryDetailView } from "@/components/library-detail-view";

/** 兜底标题；库名要等接口返回，就绪后由视图内的 usePageTitle 覆盖为「{库名}」。 */
export const metadata: Metadata = { title: "媒体库" };

/** 单库页（/library/[id]）：库信息头部 + 库内作品海报墙。 */
export default async function LibraryDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div className="flex h-full flex-col pt-5">
      <LibraryDetailView libraryId={Number(id)} />
    </div>
  );
}
