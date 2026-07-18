"use client";

import { memo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * AI 回复正文的 Markdown 渲染器。
 *
 * 选型：react-markdown —— React 生态最成熟的方案，底层是 remark/rehype
 * 插件体系（unified 生态），后续要加代码高亮（rehype-highlight/shiki）、
 * 数学公式（remark-math + rehype-katex）等只需往 plugins 数组里追加，
 * 不用换渲染器。且它把 markdown 编译为 React 元素而非 dangerouslySetInnerHTML，
 * 默认无 XSS 风险，适合渲染 LLM 输出这种不可信文本。
 *
 * 样式统一放在 globals.css 的 .markdown 作用域下（跟随 immersive-theme
 * 的灰阶变量），组件层只保留必须用 JS 表达的映射（外链新窗口打开）。
 *
 * memo：流式生成时父组件每个 chunk 都会重渲染，已完成的历史轮次
 * text 不变，跳过重新解析。
 */
const components: Components = {
  // LLM 输出里的链接一律新窗口打开，不打断当前会话
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
};

export const Markdown = memo(function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {text}
      </ReactMarkdown>
    </div>
  );
});
