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
  if (error instanceof ApiClientError || isApiClientErrorViewInput(error)) {
    if (error.statusCode === 0) {
      const timedOut = /timeout|timed\s*out|超时/iu.test(error.message);
      return {
        message: timedOut
          ? "请求超时了，请确认网络后再试一次。"
          : "网络没有连上或响应超时，请检查网络后再试一次。",
        requestId: error.requestId,
      };
    }
    if (error.statusCode === 408 || error.statusCode === 504) {
      return { message: "请求超时了，请稍后再试一次。", requestId: error.requestId };
    }
    return { message: error.message || fallback, requestId: error.requestId };
  }
  return { message: fallback, requestId: "" };
}

function isApiClientErrorViewInput(
  error: unknown,
): error is Pick<ApiClientError, "message" | "statusCode" | "requestId"> {
  if (typeof error !== "object" || error === null) return false;
  const candidate = error as Partial<Pick<ApiClientError, "message" | "statusCode" | "requestId">>;
  return (
    typeof candidate.message === "string" &&
    typeof candidate.statusCode === "number" &&
    typeof candidate.requestId === "string"
  );
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "数据不足" : `${String(Math.round(value * 100))}%`;
}

export function readinessLabel(value: string): string {
  const labels: Record<string, string> = {
    unavailable: "暂无证据",
    developing: "正在建立",
    reliable: "已有验证",
    stale: "需要更新",
    conflicted: "存在冲突",
    complete: "信息完整",
    partial: "还需补充",
  };
  return labels[value] ?? "正在核对";
}

export function decimal(value: number | null | undefined, suffix = ""): string {
  return value === null || value === undefined ? "数据不足" : `${value.toFixed(1)}${suffix}`;
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

export const trackLabel = {
  xingce: "行测",
  essay: "申论",
  recovery: "恢复",
  mock: "模考",
} as const;
