import type { Metadata } from "next";

import { AgentConversationView } from "@/components/agent-conversation-view";

/** 兜底标题；会话名在客户端 store，就绪后由视图内的 usePageTitle 覆盖为「{会话名}」。 */
export const metadata: Metadata = { title: "任务会话" };

/** Agent 会话（/runs/[id]）：ChatGPT 式对话页，会话数据在客户端 store。 */
export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AgentConversationView conversationId={id} />;
}
