"use client";

import { memo, useEffect, useState } from "react";

import { type CodeLang, highlightCode } from "@/lib/shiki";

/**
 * 异步 shiki 高亮的代码展示：高亮器就绪前先渲染纯文本 <pre> 兜底，
 * 不阻塞首屏；容器统一挂 .code-highlight（globals.css 里去掉主题
 * 自带底色、开启自动换行，保证长命令完整展示不溢出）。
 */
export const HighlightedCode = memo(function HighlightedCode({
  code,
  lang,
}: {
  code: string;
  lang: CodeLang;
}) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    highlightCode(code, lang).then((result) => {
      if (alive) setHtml(result);
    });
    return () => {
      alive = false;
    };
  }, [code, lang]);

  if (html === null) {
    return (
      <div className="code-highlight">
        <pre>
          <code>{code}</code>
        </pre>
      </div>
    );
  }
  return <div className="code-highlight" dangerouslySetInnerHTML={{ __html: html }} />;
});
