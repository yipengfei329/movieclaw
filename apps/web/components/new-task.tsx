"use client";

import { useState } from "react";

import type { Route } from "next";
import { useRouter } from "next/navigation";

import { Composer } from "@/components/composer";
import { useAgentConversations } from "@/lib/agent-conversations";

/* —— 新任务（路由 /）：仅一个居中输入框，大图氛围页直出。
     发起任务 = 创建会话并立即跳转到会话页（/runs/[id]），流式过程在会话页渲染。 —— */
export function NewTask() {
  const router = useRouter();
  const { start } = useAgentConversations();
  const [input, setInput] = useState("");
  // 创建会话需等服务端返回 session_id 才能跳转；等待期锁住输入框
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function submit(text: string) {
    setCreating(true);
    setError(null);
    start(text)
      .then((id) => {
        router.push(`/runs/${id}` as Route);
      })
      .catch((e) => {
        setError((e as Error).message);
        setCreating(false);
      });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="scroll-thin flex-1 overflow-y-auto">
        <div className="mx-auto flex min-h-full max-w-2xl flex-col justify-center px-6 py-12">
          <Composer autoFocus value={input} onChange={setInput} onSubmit={submit} busy={creating} />
          {error && (
            <p className="mt-3 rounded-xl border border-[#ff6b6b]/30 bg-[#ff6b6b]/10 px-3.5 py-2.5 text-[13px] leading-5 text-[#ff6b6b]">
              创建会话失败：{error}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
