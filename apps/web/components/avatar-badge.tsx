"use client";

import { initialsOf } from "@/lib/session";

/**
 * 用户头像徽标：上传过头像显示图片（圆形裁切），否则回退到昵称首字的银色徽标。
 * 用户菜单、设置页个人信息等所有展示头像处共用本组件，保证换头像后观感一致。
 * 尺寸与字号由调用方通过 className 指定（如 "size-9 text-[13px]"）。
 */
export function AvatarBadge({
  nickname,
  avatarUrl,
  className = "",
}: {
  nickname: string;
  avatarUrl: string | null;
  className?: string;
}) {
  return (
    <span
      className={`brand-badge flex shrink-0 items-center justify-center overflow-hidden rounded-full font-bold ${className}`}
    >
      {avatarUrl ? (
        <img src={avatarUrl} alt={`${nickname} 的头像`} className="h-full w-full object-cover" />
      ) : (
        initialsOf(nickname)
      )}
    </span>
  );
}
