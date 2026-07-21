import { request } from "@/lib/http";

/** 后端统一响应信封（见 movieclaw_api.schemas.response.ApiResponse） */
interface ApiEnvelope<T> {
  success: boolean;
  code: string;
  message: string;
  data: T;
}

/** 服务器上的一个子目录（见 schemas.fs.FsEntry）。 */
export interface FsEntry {
  name: string;
  path: string;
}

/** 一次目录浏览的结果：当前位置 + 上级 + 子目录列表。 */
export interface FsBrowse {
  path: string;
  parent: string | null;
  entries: FsEntry[];
}

/** 列出服务器上某目录的子目录（目录选择器数据源）；path 缺省为根目录。 */
export async function browseFs(path?: string): Promise<FsBrowse> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : "";
  return (await request<ApiEnvelope<FsBrowse>>(`/fs/browse${qs}`)).data;
}
