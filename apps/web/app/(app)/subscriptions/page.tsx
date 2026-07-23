import type { Metadata } from "next";

import { SubscriptionsView } from "@/components/subscriptions-view";

export const metadata: Metadata = { title: "我的订阅" };

/** 我的订阅（/subscriptions）：本地订阅影片的海报墙。 */
export default function SubscriptionsPage() {
  return (
    <div className="flex h-full flex-col pt-5">
      <SubscriptionsView />
    </div>
  );
}
