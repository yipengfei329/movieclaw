"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { getBootstrapStatus, getSession, type SessionView } from "@/lib/api/auth";
import { SessionProvider } from "@/lib/session";

/**
 * 首页的鉴权门：在确认登录状态之前不渲染工作台，消除
 * "先闪一下主界面、再跳转登录/引导页"的割裂体验。
 *
 * 判定顺序（都是一次极轻的 GET）：
 * 1. 系统未初始化 → 直接去 /setup（不再经 /login 二连跳）；
 * 2. 已初始化但未登录 → getSession 得到 401，由 http.ts 统一跳 /login；
 * 3. 已登录 → 把会话数据注入 SessionProvider，放行渲染工作台。
 * 等待期间只显示 body 自带的背景大图（启动页效果），不渲染任何业务 UI。
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<SessionView | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getBootstrapStatus();
        if (cancelled) return;
        if (!status.initialized) {
          router.replace("/setup");
          return;
        }
        const view = await getSession(); // 未登录时抛 401，http.ts 拦截并整页跳 /login
        if (!cancelled) setSession(view);
      } catch {
        // 401 的跳转已由 http.ts 发起；其他异常（后端未起）保持启动页，
        // 用户刷新即可重试，不至于把挂掉的工作台渲染出来。
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!session) {
    // 启动页：背景大图由 body::before 提供，这里不需要渲染任何内容
    return null;
  }
  return <SessionProvider value={{ session, setSession }}>{children}</SessionProvider>;
}
