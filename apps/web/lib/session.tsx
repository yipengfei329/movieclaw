"use client";

import { createContext, useContext } from "react";

import type { SessionView } from "@/lib/api/auth";

/**
 * 登录会话的全局上下文：由 AuthGate 在确认登录后注入，
 * 供用户菜单、设置页个人信息等处读取真实账号数据。
 * setSession 用于"改昵称后立即同步所有展示处"，不必整页刷新。
 */
interface SessionContextValue {
  session: SessionView;
  setSession: (session: SessionView) => void;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({
  value,
  children,
}: {
  value: SessionContextValue;
  children: React.ReactNode;
}) {
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

/** 读取当前登录会话。必须在 AuthGate（SessionProvider）内使用。 */
export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession 必须在 <SessionProvider> 内使用");
  return ctx;
}

/** 由昵称生成头像徽标字：中文取首字，拉丁字母取前两位大写。 */
export function initialsOf(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "?";
  if (/[一-鿿]/.test(trimmed[0])) return trimmed[0];
  return trimmed.slice(0, 2).toUpperCase();
}
