"use client";

import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createJavaScriptRegexEngine } from "shiki/engine/javascript";

/**
 * 全站共享的 shiki 高亮器单例（工具调用卡片的输入参数展示用）。
 *
 * 细粒度按需装载：只带 bash / json 两个语法 + 一个暗色主题，用纯 JS
 * 正则引擎（不加载 oniguruma wasm），首次高亮时才异步初始化。
 * 后续要支持更多语言，往 langs 数组里追加动态 import 即可。
 */
export type CodeLang = "bash" | "json";

let highlighterPromise: Promise<HighlighterCore> | null = null;

function getHighlighter(): Promise<HighlighterCore> {
  highlighterPromise ??= createHighlighterCore({
    themes: [import("@shikijs/themes/github-dark")],
    langs: [import("@shikijs/langs/bash"), import("@shikijs/langs/json")],
    engine: createJavaScriptRegexEngine(),
  });
  return highlighterPromise;
}

/** 把代码渲染成带主题色的 HTML（shiki 会转义内容，可安全注入）。 */
export async function highlightCode(code: string, lang: CodeLang): Promise<string> {
  const highlighter = await getHighlighter();
  return highlighter.codeToHtml(code, { lang, theme: "github-dark" });
}
