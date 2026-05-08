import { request } from "@/lib/http";

export interface HealthResponse {
  status: string;
  service: string;
  environment: string;
}

export function getHealth(init?: RequestInit): Promise<HealthResponse> {
  return request<HealthResponse>("/health", init);
}
