"use client";

import { useState, type ReactNode } from "react";

/**
 * 海报图片底座：全站所有海报/封面 <img> 的统一实现。
 *
 * 统一收口三件事，调用方不必各自重复：
 *   1. loading="lazy" —— 海报墙一页几十张图，懒加载是默认约定；
 *   2. referrerPolicy="no-referrer" —— 豆瓣等图床按 Referer 拒绝外链，不带即可正常加载；
 *   3. 加载失败回退 —— 防盗链 / 图床失效时渲染深色占位（或调用方自定义的 fallback），
 *      卡片不塌陷、不出裂图图标。
 *
 * 定位、圆角、hover 缩放等布局差异全部通过 className 由调用方传入；
 * 占位符会套用同一份 className，保证与图片占据完全相同的盒子。
 * 组件只负责「一张海报图」本身，卡片语义（徽章、渐变信息层、点击行为）留给上层。
 */
export function PosterImage({
  src,
  alt,
  className = "",
  fallback,
}: {
  /** 图片地址；为空时直接渲染占位 */
  src?: string | null;
  alt: string;
  /** 应用在 <img> 与默认占位上的布局类（定位 / 尺寸 / 过渡等） */
  className?: string;
  /** 自定义占位内容；不传则渲染深色渐变底 */
  fallback?: ReactNode;
}) {
  const [broken, setBroken] = useState(false);
  if (!src || broken) {
    return (
      fallback ?? (
        <div
          aria-hidden="true"
          className={`bg-gradient-to-b from-white/[0.05] to-[#141824] ${className}`}
        />
      )
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setBroken(true)}
      className={`bg-[#141824] object-cover ${className}`}
    />
  );
}
