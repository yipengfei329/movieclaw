import type { Metadata } from "next";

import { Top250View } from "@/components/top250-view";

export const metadata: Metadata = { title: "豆瓣电影 Top 250" };

/** 豆瓣电影 Top 250 完整榜单：网格浏览、片名搜索与分批加载。 */
export default function Top250Page() {
  return (
    <div className="flex h-full flex-col">
      <Top250View />
    </div>
  );
}
