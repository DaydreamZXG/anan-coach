import { ApiClient, ApiClientError } from "../../api/client";
import { currentAccessToken } from "../../auth/session";

export function authenticatedClient(): ApiClient | null {
  const token = currentAccessToken();
  return token ? new ApiClient(undefined, token) : null;
}

export function requireAuthenticatedClient(): ApiClient | null {
  const client = authenticatedClient();
  if (!client) void wx.reLaunch({ url: "/pages/login/index" });
  return client;
}

export function errorView(
  error: unknown,
  fallback: string,
): { message: string; requestId: string } {
  if (error instanceof ApiClientError) {
    return { message: error.message || fallback, requestId: error.requestId };
  }
  return { message: fallback, requestId: "" };
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "证据不足" : `${String(Math.round(value * 100))}%`;
}

export function decimal(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined ? "证据不足" : `${value.toFixed(1)}${suffix}`;
}

export function shortDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

export function dateTimeLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return `${shortDate(value)} ${String(date.getHours()).padStart(2, "0")}:${String(
    date.getMinutes(),
  ).padStart(2, "0")}`;
}

export function newIdempotencyKey(scope: string): string {
  return `${scope}-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

export const trackLabel = {
  xingce: "行测",
  essay: "申论",
  recovery: "恢复",
  mock: "模考",
} as const;
