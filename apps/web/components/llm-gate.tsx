"use client";

import { useEffect, useState } from "react";

import Link from "next/link";

import { getLlmProvider } from "@/lib/api/llm";

/* —— LLM 配置门禁：对话入口在未接入模型供应商时锁定输入框并引导去设置。
     判定与后端 acquire_llm_router 对齐——只看「是否已配置」（GET /llm/provider
     是否为 null），验证失败的配置后端仍会尝试使用，不在前端拦截。 —— */

/**
 * 探测模型供应商是否已配置。
 *
 * 返回三态：null = 探测中（不锁定，避免闪烁）；true = 已配置；false = 未配置。
 * 探测失败（如后端未就绪）按 null 处理，交给提交时的服务端错误兜底。
 */
export function useLlmConfigured(): boolean | null {
  const [configured, setConfigured] = useState<boolean | null>(null);
  useEffect(() => {
    let cancelled = false;
    getLlmProvider()
      .then((config) => {
        if (!cancelled) setConfigured(config != null);
      })
      .catch(() => {
        // 探测接口本身出错时不锁输入框：宁可放行让提交报错，也不误锁
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return configured;
}

/** 未配置模型时的引导提示：告知原因 + 一键跳转设置页。 */
export function LlmSetupNotice() {
  return (
    <p className="mt-3 rounded-xl border border-[var(--glass-stroke)] bg-[var(--glass-fill)] px-3.5 py-2.5 text-[13px] leading-5 text-[var(--text-muted)]">
      尚未接入 AI 模型，暂时无法开始对话。请先前往
      <Link href="/settings/llm" className="mx-0.5 text-[var(--accent)] hover:underline">
        设置 → AI 模型
      </Link>
      完成配置。
    </p>
  );
}
