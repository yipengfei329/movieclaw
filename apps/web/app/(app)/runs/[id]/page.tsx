import { AgentConversationView } from "@/components/agent-conversation-view";

/** Agent 会话（/runs/[id]）：ChatGPT 式对话页，会话数据在客户端 store。 */
export default async function RunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <AgentConversationView conversationId={id} />;
}
