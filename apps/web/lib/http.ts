import { publicEnv } from "@/lib/env";

export class HttpError extends Error {
  status: number;
  details: unknown;

  constructor(message: string, status: number, details: unknown) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    this.details = details;
  }
}

function trimTrailingSlash(value: string): string {
  return value !== "/" && value.endsWith("/") ? value.slice(0, -1) : value;
}

function trimLeadingSlash(value: string): string {
  return value.startsWith("/") ? value.slice(1) : value;
}

function resolveRequestUrl(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path;
  }

  const baseUrl = trimTrailingSlash(publicEnv.apiBaseUrl);
  const requestPath = trimLeadingSlash(path);

  if (baseUrl === "/") {
    return `/${requestPath}`;
  }

  return `${baseUrl}/${requestPath}`;
}

function buildHeaders(initHeaders?: HeadersInit, body?: BodyInit | null): HeadersInit {
  const headers = new Headers(initHeaders);
  headers.set("Accept", "application/json");

  if (body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  return headers;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(resolveRequestUrl(path), {
    ...init,
    headers: buildHeaders(init.headers, init.body),
  });

  if (response.status === 204) {
    return undefined as T;
  }

  const contentType = response.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const message =
      isJson && payload && typeof payload === "object" && "message" in payload
        ? String(payload.message)
        : `Request failed with status ${response.status}`;

    throw new HttpError(message, response.status, payload);
  }

  return payload as T;
}
