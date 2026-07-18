"use client";

import { useState } from "react";

import type { Route } from "next";
import { useRouter } from "next/navigation";

import { Composer } from "@/components/composer";
import { SparkIcon } from "@/components/icons";
import { useAgentConversations } from "@/lib/agent-conversations";

/* —— 新任务（路由 /）：标题 + 输入框居中，类 ChatGPT 首屏。
     发起任务 = 创建会话并立即跳转到会话页（/runs/[id]），流式过程在会话页渲染。 —— */
export function NewTask() {
  const router = useRouter();
  const { start } = useAgentConversations();
  const [input, setInput] = useState("");
  // 创建会话需等服务端返回 session_id 才能跳转；等待期锁住输入框
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suggestions = [
    "帮我订阅《三体》第二季，有 4K 就自动下载",
    "找《奥本海默》评分最高的中字资源",
    "每周一自动巡检豆瓣高分新片",
    "清理做种率低于 0.5 的历史任务",
  ];

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
          <p className="text-on-image mb-4 text-center text-[11px] font-semibold uppercase tracking-[0.22em] text-[var(--accent)]">
            影视追踪工作台
          </p>
          <h2 className="text-on-image text-center text-[34px] font-semibold leading-[1.15] tracking-[-0.02em] text-white">
            我们在 movieclaw 里做点什么？
          </h2>
          <p className="text-on-image mx-auto mt-4 max-w-md text-center text-[14px] leading-6 text-[rgba(243,245,249,0.82)]">
            用一句话描述你想追踪的影视资源，movieclaw 会跨站点检索、下载并持续维护。
          </p>

          <div className="mt-8">
            <Composer autoFocus value={input} onChange={setInput} onSubmit={submit} busy={creating} />
            {error && (
              <p className="mt-3 rounded-xl border border-[#ff6b6b]/30 bg-[#ff6b6b]/10 px-3.5 py-2.5 text-[13px] leading-5 text-[#ff6b6b]">
                创建会话失败：{error}
              </p>
            )}
          </div>

          <div className="mt-4 grid gap-2 sm:grid-cols-2">
            {suggestions.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setInput(s)}
                className="group flex items-center gap-2.5 rounded-2xl border border-white/[0.07] bg-[rgba(14,16,22,0.42)] px-4 py-3.5 text-left text-[13px] leading-5 text-[rgba(243,245,249,0.82)] backdrop-blur-xl transition-all hover:-translate-y-0.5 hover:bg-[rgba(20,23,31,0.6)] hover:text-[var(--text)]"
              >
                <SparkIcon className="size-4 shrink-0 text-[var(--text-faint)] transition-colors group-hover:text-[var(--accent-2)]" />
                <span className="flex-1">{s}</span>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
