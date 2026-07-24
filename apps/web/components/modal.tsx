"use client";

/**
 * 通用居中弹窗基座——全局唯一的模态骨架，所有居中卡片弹窗基于它封装。
 *
 * 统一吸收三件容易漏抄/踩坑的事：
 * 1. **createPortal 挂到 document.body**：液态玻璃 UI 里列表行/卡片普遍带
 *    backdrop-filter，而带 backdrop-filter 的祖先会成为 position:fixed 的
 *    包含块——就地渲染的弹窗会被困在触发元素内、被相邻元素遮挡
 *    （搜索结果下载弹窗踩过的坑）。portal 从结构上杜绝这一类 bug。
 * 2. **遮罩 + Esc 关闭 + aria 模态语义**：一处实现，处处一致。
 * 3. **玻璃面板视觉**（圆角/描边/阴影/毛玻璃）收敛为一份，改一处全局生效。
 *
 * 页面弹窗在外层包业务壳（如 download-target-dialog、subscribe-dialog），
 * 面板内部布局（滚动区/头部/底栏）由 children 自理；需要定制时用 width 换
 * 宽度档位、panelClassName 追加布局类（如 "flex max-h-[76vh] flex-col"）。
 *
 * 嵌套弹窗（弹窗内再开弹窗，如表单里的目录选择器）：上层置 raised 抬高
 * z 层级；上层若需拦截 Esc（如输入态只退输入不关弹窗），自行在 capture
 * 阶段监听并 stopPropagation，本组件的冒泡阶段监听即不会触发。
 */

import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";

/** 面板宽度档位：表单类弹窗 md；内容较多的 lg；预览/清单类 2xl；
 *  full 为沉浸查看类（日志全屏等）铺满视口，高度由调用方经 panelClassName 撑满。 */
const WIDTH_CLS = {
  md: "max-w-md",
  lg: "max-w-lg",
  "2xl": "max-w-2xl",
  full: "max-w-none",
} as const;

export function Modal({
  open,
  onClose,
  label,
  width = "md",
  raised = false,
  panelClassName = "",
  children,
}: {
  /** false 时不渲染任何内容 */
  open: boolean;
  /** 点遮罩 / 按 Esc 触发 */
  onClose: () => void;
  /** 弹窗的无障碍名称（aria-label） */
  label: string;
  width?: keyof typeof WIDTH_CLS;
  /** 叠在其他弹窗之上时置 true（z-60 > 普通弹窗的 z-50） */
  raised?: boolean;
  /** 追加到玻璃面板容器的类（定制布局，如 flex 限高列布局） */
  panelClassName?: string;
  children: ReactNode;
}) {
  // Esc 关闭（冒泡阶段，可被上层弹窗的 capture 监听拦截，见文件头注释）
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div
      className={`fixed inset-0 ${raised ? "z-[60]" : "z-50"} flex items-center justify-center p-6`}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      // portal 后 React 合成事件仍沿组件树冒泡——弹窗常由列表行内的按钮触发，
      // 拦掉点击以免误触发触发元素自身的点击行为
      onClick={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-label="关闭"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-black/60 backdrop-blur-sm"
      />
      <div
        className={`relative w-full ${WIDTH_CLS[width]} overflow-hidden rounded-2xl border border-white/10 bg-[rgba(16,18,26,0.92)] shadow-[0_32px_90px_rgba(0,0,0,0.7)] backdrop-blur-2xl ${panelClassName}`}
      >
        {children}
      </div>
    </div>,
    document.body,
  );
}
