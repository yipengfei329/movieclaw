"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * MovieClaw 浏览器插件的安装分发与检测。
 *
 * 插件 ID 恒定：apps/extension/wxt.config.ts 的 manifest 里固定了公钥 `key`，
 * Chrome 由公钥推导插件 ID——无论用户从哪台机器「加载已解压的扩展程序」，
 * ID 都是下面这个值。若 wxt.config.ts 换了 key，此处必须同步更新。
 *
 * 检测原理：插件把 movieclaw-marker.json 声明为 web_accessible_resources，
 * 页面 fetch chrome-extension://<ID>/movieclaw-marker.json 成功 = 已安装。
 * 仅 Chromium 系（Chrome / Edge）有效；Firefox 的扩展 ID 随安装随机，
 * fetch 必然失败，会被判为「未检测到」。
 */
export const EXTENSION_ID = "hhjihoefiocbpmnoohlkmeiaiplpadhj";

/** 插件包下载地址：pnpm ext:publish 把 zip 发布到 apps/web/public/extension/ */
export const EXTENSION_ZIP_URL = "/extension/movieclaw-extension.zip";

/**
 * 探测插件是否已安装。
 * 返回 null = 检测中，true/false = 结果；窗口重新获得焦点时自动复测
 * （典型场景：用户切去 chrome://extensions 装完插件再切回来，状态即时变绿）。
 */
export function useExtensionInstalled(): { installed: boolean | null; recheck: () => void } {
  const [installed, setInstalled] = useState<boolean | null>(null);

  const recheck = useCallback(() => {
    let cancelled = false;
    fetch(`chrome-extension://${EXTENSION_ID}/movieclaw-marker.json`, { cache: "no-store" })
      .then((r) => {
        if (!cancelled) setInstalled(r.ok);
      })
      .catch(() => {
        if (!cancelled) setInstalled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const cancel = recheck();
    const onFocus = () => void recheck();
    window.addEventListener("focus", onFocus);
    return () => {
      cancel();
      window.removeEventListener("focus", onFocus);
    };
  }, [recheck]);

  return { installed, recheck };
}
