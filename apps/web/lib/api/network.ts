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

/** 走代理开关目录里的一项（内置服务或 site:<id> 形式的 PT 站）。 */
export interface EgressServiceOption {
  id: string;
  label: string;
  description: string;
}

/** 代理模式：off 全部直连 / env 跟随环境变量 / manual 手动填写。 */
export type ProxyMode = "off" | "env" | "manual";

/** 保存请求体（见 routes/network.NetworkConfigPayload）。 */
export interface NetworkConfigPayload {
  proxy_mode: ProxyMode;
  proxy_url: string;
  proxy_services: string[];
  tmdb_api_base_url: string;
  tmdb_image_base_url: string;
  douban_api_base_url: string;
}

/** 读取响应：配置本体 + 渲染所需的目录与默认值。 */
export interface NetworkConfigView extends NetworkConfigPayload {
  services: EgressServiceOption[];
  /** 三个镜像地址的生效默认值（设置为空时的回落，供 placeholder 展示） */
  mirror_defaults: Record<string, string>;
  /** 环境变量中探测到的代理地址；env 模式下供用户确认 */
  env_proxy_detected: string;
}

export interface NetworkTestResult {
  ok: boolean;
  latency_ms: number | null;
  message: string;
}

/** 读取网络与代理配置。 */
export function getNetworkConfig(): Promise<NetworkConfigView> {
  return unwrap(request<ApiEnvelope<NetworkConfigView>>("/network/config"));
}

/** 保存网络与代理配置（后端立即生效，无需重启）。 */
export function saveNetworkConfig(payload: NetworkConfigPayload): Promise<NetworkConfigView> {
  return unwrap(
    request<ApiEnvelope<NetworkConfigView>>("/network/config", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  );
}

/** 对某服务做一次连通性测试（走当前已保存的出口配置）。 */
export function testNetworkService(service: string): Promise<NetworkTestResult> {
  return unwrap(
    request<ApiEnvelope<NetworkTestResult>>("/network/test", {
      method: "POST",
      body: JSON.stringify({ service }),
    }),
  );
}
