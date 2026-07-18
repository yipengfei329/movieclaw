import { SubscriptionInspectorView } from "@/components/subscription-inspector-view";

/** 订阅详情分析页（/subscriptions/[id]）：追踪明细 + 活动时间线。 */
export default async function SubscriptionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return (
    <div className="flex h-full flex-col pt-5">
      <SubscriptionInspectorView id={Number(id)} />
    </div>
  );
}
