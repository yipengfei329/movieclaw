import type { Metadata } from "next";

import { LibraryView } from "@/components/library-view";

export const metadata: Metadata = { title: "媒体库" };

/** 媒体库（/library）：全部库的 Emby 风格卡片墙，内容的一等入口。 */
export default function LibraryPage() {
  return (
    <div className="flex h-full flex-col pt-5">
      <LibraryView />
    </div>
  );
}
