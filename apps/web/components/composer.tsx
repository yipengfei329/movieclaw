"use client";

import { useState } from "react";

import { GlassPanel } from "@/components/glass-panel";
import { PlusIcon, SendIcon } from "@/components/icons";
import { useBackdrop } from "@/lib/backdrop";
import { LiquidGlassIconButton } from "@/vendor/liquid-glass";

export interface ComposerProps {
  autoFocus?: boolean;
  /** 受控值（与 onChange 配套）；不传则组件内部管理输入状态 */
  value?: string;
  onChange?: (value: string) => void;
  /** 提交回调（回车或点发送）；不传时输入框为纯展示，发送不可用 */
  onSubmit?: (text: string) => void;
  /** 生成中：提交被阻断；配合 onStop 时发送键变为停止键（仿 ChatGPT） */
  busy?: boolean;
  /** 停止生成回调；仅在 busy 时生效 */
  onStop?: () => void;
  /** 纯色模式：给阅读面板（对话页）用——不渲染 WebGL 玻璃、不折射背景大图，
   * 避免在不透明底色上把海报纹理透回来；首页氛围页保持默认玻璃形态 */
  flat?: boolean;
}

/* —— 输入框：Codex 风格的无边框输入区（固定 2 行高，超出在框内滚动） —— */
export function Composer({
  autoFocus = false,
  value,
  onChange,
  onSubmit,
  busy = false,
  onStop,
  flat = false,
}: ComposerProps) {
  const { backdrop } = useBackdrop();
  // 未受控时的内部状态（纯展示场景仍可直接 <Composer />）
  const [inner, setInner] = useState("");
  const text = value ?? inner;
  const setText = onChange ?? setInner;
  const canSubmit = !busy && text.trim().length > 0 && onSubmit != null;
  // 生成中且可停止：发送键位变为停止键
  const showStop = busy && onStop != null;

  function submit() {
    if (!canSubmit) return;
    onSubmit?.(text.trim());
  }

  const body = (
    <>
      <textarea
        rows={2}
        autoFocus={autoFocus}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // 回车提交、Shift+回车换行（输入法组合期间的回车不触发）
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={busy ? "生成中，可先输入下一条…" : "随心输入，描述一个新任务…"}
        className="scroll-thin block w-full resize-none bg-transparent px-4 pb-1 pt-3.5 text-[14px] leading-6 text-[var(--text)] placeholder:text-[var(--text-faint)] focus:outline-none"
      />
      <div className="flex items-center justify-between px-2.5 pb-2.5 pt-1">
        <button
          type="button"
          aria-label="添加附件"
          className="flex size-8 items-center justify-center rounded-xl text-[var(--text-muted)] transition-colors hover:bg-[var(--glass-fill-hover)] hover:text-[var(--text)]"
        >
          <PlusIcon className="size-[18px]" />
        </button>
        <div className="flex items-center gap-2">
          <span className="hidden text-[11px] text-[var(--text-faint)] sm:block">
            {showStop ? (
              "生成中"
            ) : (
              <>
                <kbd className="tnum rounded bg-white/[0.06] px-1 py-0.5 font-sans">⏎</kbd> 发送
              </>
            )}
          </span>
          {flat ? (
            // 纯色模式的发送 / 停止键：普通实色按钮
            <button
              type="button"
              disabled={!showStop && !canSubmit}
              onClick={() => (showStop ? onStop?.() : submit())}
              aria-label={showStop ? "停止生成" : "发送"}
              className="flex size-9 items-center justify-center rounded-[12px] bg-white/[0.1] text-[var(--text)] transition-colors hover:bg-white/[0.16] disabled:opacity-40 disabled:hover:bg-white/[0.1]"
            >
              {showStop ? (
                <span className="block size-[11px] rounded-[3px] bg-current" />
              ) : (
                <SendIcon className="size-[18px]" />
              )}
            </button>
          ) : (
            /* 发送 / 停止：真实 WebGL 液态玻璃按钮，点击经 onActiveChange 触发。 */
            <LiquidGlassIconButton
              backgroundImage={backdrop}
              variant="dark"
              shape="squircle"
              width={36}
              height={36}
              active={false}
              disabled={!showStop && !canSubmit}
              onActiveChange={() => (showStop ? onStop?.() : submit())}
              aria-label={showStop ? "停止生成" : "发送"}
              className="lg-send !size-9"
            >
              {showStop ? (
                // 停止：ChatGPT 同款实心方块
                <span className="block size-[11px] rounded-[3px] bg-current" />
              ) : (
                <SendIcon className="!size-[18px]" />
              )}
            </LiquidGlassIconButton>
          )}
        </div>
      </div>
    </>
  );

  if (flat) {
    // 阅读面板上的纯色内嵌卡片：与全站输入框同圆角，发丝描边做边界
    return (
      <div className="rounded-[22px] bg-[var(--surface-inset)] shadow-[inset_0_0_0_1px_rgba(255,255,255,0.07)]">
        {body}
      </div>
    );
  }
  return (
    // 输入框本身是一块真实液态玻璃卡片：折射下方背景大图，浮于内容区之上。
    <GlassPanel
      backgroundImage={backdrop}
      variant="dark"
      radius={22}
      className="composer-shell"
      settings={{ darkTint: 0.42, blur: 0.22, brightness: -0.05 }}
    >
      {body}
    </GlassPanel>
  );
}
