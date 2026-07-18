"use client";

import { GlassPanel } from "@/components/glass-panel";
import { BackdropProvider, useBackdrop } from "@/lib/backdrop";
import { publicEnv } from "@/lib/env";

/**
 * 登录 / 初始化引导两个页面共用的全屏外壳：
 * 背景大图之上居中悬浮一块液态玻璃卡片，与工作台的视觉语言一致。
 *
 * BackdropProvider 在此单独包一层（这两个页面不在 AppShell 内），
 * 使自定义背景图在登录页同样生效——对应后端把外观读取接口放进了公开白名单。
 */
export function AuthScreen({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <BackdropProvider>
      <AuthScreenInner title={title} subtitle={subtitle}>
        {children}
      </AuthScreenInner>
    </BackdropProvider>
  );
}

function AuthScreenInner({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  const { backdrop } = useBackdrop();

  return (
    <div className="relative z-10 flex h-screen w-screen items-center justify-center p-6">
      <div className="w-full max-w-[400px]">
        {/* 表单是整页文字最密的区域，玻璃必须压得比工作台面板更暗才能保住对比度：
            在主区输入框的配方（darkTint 0.42 / blur 0.22）基础上再深一档。 */}
        <GlassPanel
          backgroundImage={backdrop}
          radius={20}
          settings={{ darkTint: 0.52, blur: 0.32, brightness: -0.06 }}
          contentClassName="p-8"
        >
          <header className="mb-7">
            <p className="text-[12px] font-semibold uppercase tracking-[0.18em] text-[var(--text-faint)]">
              {publicEnv.appName}
            </p>
            <h1 className="mt-2 text-[22px] font-semibold text-[var(--text)]">{title}</h1>
            <p className="mt-1.5 text-[13px] leading-relaxed text-[var(--text-muted)]">
              {subtitle}
            </p>
          </header>
          {children}
        </GlassPanel>
      </div>
    </div>
  );
}

/** 表单字段：沿用设置页站点配置表单的输入框样式。 */
export function AuthField({
  label,
  ...inputProps
}: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <div>
      <label className="mb-1.5 block text-xs font-medium text-[var(--text-muted)]">{label}</label>
      <input
        {...inputProps}
        className="w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-2 text-[13px] text-[var(--text)] outline-none focus:border-[var(--accent)]/60"
      />
    </div>
  );
}

/** 表单级错误提示（登录失败 / 校验不通过 / 限速提示）。 */
export function AuthError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="rounded-xl border border-[rgba(255,107,107,0.25)] bg-[rgba(255,107,107,0.1)] px-3 py-2 text-[12px] leading-relaxed text-[var(--danger)]">
      {message}
    </p>
  );
}
