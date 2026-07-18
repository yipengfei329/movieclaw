import { request } from "@/lib/http";

/** 后端统一响应信封（见 movieclaw_api.schemas.response.ApiResponse） */
interface ApiEnvelope<T> {
  success: boolean;
  code: string;
  message: string;
  data: T;
}

async function unwrap<T>(promise: Promise<ApiEnvelope<T>>): Promise<T> {
  return (await promise).data;
}

/** 当前登录会话（见 schemas.auth.SessionView）。 */
export interface SessionView {
  username: string;
  /** 展示昵称；建号时默认取用户名，可在「个人信息」里修改 */
  nickname: string;
  /** 头像相对 URL（含版本号，换头像后 URL 变化以绕开缓存）；未上传过为空 */
  avatar_url: string | null;
}

/** 首次初始化状态：未初始化时前端应进 /setup 引导页。 */
export interface BootstrapStatus {
  initialized: boolean;
}

/** 查询系统是否已完成首次初始化（公开接口）。 */
export function getBootstrapStatus(): Promise<BootstrapStatus> {
  return unwrap(request<ApiEnvelope<BootstrapStatus>>("/auth/bootstrap"));
}

/**
 * 首次初始化：创建超级管理员并自动登录（会话 Cookie 由后端种下）。
 * 服务端持有一次性锁：管理员已存在时返回 409，本调用会抛 HttpError。
 */
export function createAdmin(username: string, password: string): Promise<SessionView> {
  return unwrap(
    request<ApiEnvelope<SessionView>>("/auth/bootstrap", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  );
}

/** 管理员登录。remember 为 true 时会话有效期 7 天 → 30 天。 */
export function login(
  username: string,
  password: string,
  remember: boolean,
): Promise<SessionView> {
  return unwrap(
    request<ApiEnvelope<SessionView>>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, remember }),
    }),
  );
}

/** 退出登录（清除会话 Cookie；会话已过期时调用也不会报错）。 */
export function logout(): Promise<void> {
  return unwrap(request<ApiEnvelope<void>>("/auth/logout", { method: "POST" }));
}

/** 查询当前登录状态；未登录时抛 401（由 http.ts 统一跳转登录页）。 */
export function getSession(): Promise<SessionView> {
  return unwrap(request<ApiEnvelope<SessionView>>("/auth/me"));
}

/** 修改展示昵称（登录用户名不可改）。 */
export function updateProfile(nickname: string): Promise<SessionView> {
  return unwrap(
    request<ApiEnvelope<SessionView>>("/auth/profile", {
      method: "PUT",
      body: JSON.stringify({ nickname }),
    }),
  );
}

/** 上传（替换）头像。file 为已压缩的图片 Blob（通常是 JPEG）。 */
export function uploadAvatar(file: Blob): Promise<SessionView> {
  const form = new FormData();
  form.append("file", file, "avatar.jpg");
  return unwrap(
    request<ApiEnvelope<SessionView>>("/auth/avatar", {
      method: "POST",
      body: form,
    }),
  );
}

/** 修改管理员密码：其余设备的会话全部强制下线，本会话自动续期。 */
export function changePassword(
  oldPassword: string,
  newPassword: string,
): Promise<SessionView> {
  return unwrap(
    request<ApiEnvelope<SessionView>>("/auth/password", {
      method: "PUT",
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }),
  );
}
