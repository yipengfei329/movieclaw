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

/** 图库中的一张背景图（见 schemas.appearance.BackdropItem）。 */
export interface BackdropItem {
  /** 背景图 id（uuid4 hex） */
  id: string;
  /** 图片文件的相对 URL（含版本号，换图后 URL 变化以绕开缓存） */
  url: string;
}

/** 外观设置视图（见 schemas.appearance.AppearanceView）。 */
export interface AppearanceView {
  /** 当前生效的背景图 id；为空表示使用内置默认背景。 */
  active_id: string | null;
  /** 当前生效背景图的相对 URL（含版本号）；为空表示内置默认。 */
  active_url: string | null;
  /** 图库中的全部自定义背景图（上传时间升序，全部保留供切换）。 */
  backdrops: BackdropItem[];
}

/** 读取当前外观设置（背景图库与生效图）。 */
export function getAppearance(init?: RequestInit): Promise<AppearanceView> {
  return unwrap(request<ApiEnvelope<AppearanceView>>("/appearance", init));
}

/** 上传一张新背景图：加入图库并立即设为生效。file 为已压缩的图片 Blob（通常是 JPEG）。 */
export function uploadBackdrop(file: Blob): Promise<AppearanceView> {
  const form = new FormData();
  form.append("file", file, "backdrop.jpg");
  return unwrap(
    request<ApiEnvelope<AppearanceView>>("/appearance/backdrops", {
      method: "POST",
      body: form,
    }),
  );
}

/** 切换当前生效的背景图；传 null 切回内置默认（不删除任何图）。 */
export function setActiveBackdrop(backdropId: string | null): Promise<AppearanceView> {
  return unwrap(
    request<ApiEnvelope<AppearanceView>>("/appearance/active", {
      method: "PUT",
      body: JSON.stringify({ backdrop_id: backdropId }),
    }),
  );
}

/** 从图库删除一张背景图；删的是生效图时后端自动回退内置默认。 */
export function deleteBackdrop(backdropId: string): Promise<AppearanceView> {
  return unwrap(
    request<ApiEnvelope<AppearanceView>>(`/appearance/backdrops/${backdropId}`, {
      method: "DELETE",
    }),
  );
}
