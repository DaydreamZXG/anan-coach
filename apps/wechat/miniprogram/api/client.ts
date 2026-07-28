import { environment } from "../config/environment";
import type { ApiEnvelope, ApiErrorPayload } from "../domain/contracts";
import { currentAuthSession, type ActiveAuthSession } from "../auth/session";

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

export interface ApiRequestOptions {
  idempotencyKey?: string;
  timeoutMs?: number;
  taskEditLease?: { leaseId: string; generation: number };
}

/** A response was received, or transport completion is still unknown to the caller. */
export type ApiClientErrorClassification = "http_response" | "transport_unknown";

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
    readonly requestId: string,
    readonly payload?: ApiErrorPayload,
    readonly classification: ApiClientErrorClassification = statusCode === 0
      ? "transport_unknown"
      : "http_response",
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export type UnauthorizedHandler = (
  origin: ActiveAuthSession | undefined,
  requestId: string,
) => void;

let onUnauthorized: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

function createRequestId(): string {
  return `mp-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

const FALLBACK_ERROR_MESSAGE = "刚才这一步没有成功，请稍后再试一次。";

/**
 * 界面只展示人话（规范 §6）：服务端 message 缺失、非中文或带
 * snake_case 等技术标记时一律换成统一安抚话术，requestId 照常保留。
 */
function humanReadableMessage(message: string | undefined): string {
  const trimmed = message?.trim();
  if (!trimmed) return FALLBACK_ERROR_MESSAGE;
  if (!/[一-鿿]/.test(trimmed)) return FALLBACK_ERROR_MESSAGE;
  if (/[a-z0-9]+_[a-z0-9_]+/i.test(trimmed) || trimmed.length > 80) return FALLBACK_ERROR_MESSAGE;
  return trimmed;
}

function isApiErrorPayload(value: unknown): value is ApiErrorPayload {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<ApiErrorPayload>;
  return (
    typeof candidate.code === "string" &&
    typeof candidate.message === "string" &&
    typeof candidate.requestId === "string"
  );
}

export class ApiClient {
  constructor(
    private readonly baseUrl = environment.apiBaseUrl,
    private readonly accessToken?: string,
  ) {}

  get<T>(path: string, options?: ApiRequestOptions): Promise<ApiEnvelope<T>> {
    return this.send<T>(path, "GET", undefined, options);
  }

  post<TResponse>(
    path: string,
    body: object,
    options?: ApiRequestOptions,
  ): Promise<ApiEnvelope<TResponse>> {
    return this.send<TResponse>(path, "POST", body, options);
  }

  put<TResponse>(
    path: string,
    body: object,
    options?: ApiRequestOptions,
  ): Promise<ApiEnvelope<TResponse>> {
    return this.send<TResponse>(path, "PUT", body, options);
  }

  delete<TResponse>(path: string, options?: ApiRequestOptions): Promise<ApiEnvelope<TResponse>> {
    return this.send<TResponse>(path, "DELETE", undefined, options);
  }

  private send<T>(
    path: string,
    method: HttpMethod,
    data?: object,
    options?: ApiRequestOptions,
  ): Promise<ApiEnvelope<T>> {
    const correlationId = createRequestId();
    const originSession = captureOriginSession(this.accessToken);
    return new Promise((resolve, reject) => {
      const requestOptions: WechatMiniprogram.RequestOption<ApiEnvelope<T>> = {
        url: `${this.baseUrl}${path}`,
        method,
        timeout: options?.timeoutMs ?? 15_000,
        header: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Request-ID": correlationId,
          ...(options?.idempotencyKey ? { "Idempotency-Key": options.idempotencyKey } : {}),
          ...(options?.taskEditLease
            ? {
                "X-Task-Edit-Lease": options.taskEditLease.leaseId,
                "X-Task-Edit-Generation": String(options.taskEditLease.generation),
              }
            : {}),
          ...(this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {}),
        },
        success: (response) => {
          if (response.statusCode >= 200 && response.statusCode < 300) {
            resolve(response.data);
            return;
          }

          const payload: unknown = response.data;
          const publicError = isApiErrorPayload(payload) ? payload : undefined;
          if (response.statusCode === 401) {
            onUnauthorized?.(originSession, publicError?.requestId ?? correlationId);
          }
          reject(
            new ApiClientError(
              humanReadableMessage(publicError?.message),
              response.statusCode,
              publicError?.requestId ?? correlationId,
              publicError,
            ),
          );
        },
        fail: () => {
          reject(new ApiClientError("网络请求失败。", 0, correlationId));
        },
      };
      if (data) requestOptions.data = data;
      wx.request<ApiEnvelope<T>>(requestOptions);
    });
  }
}

function captureOriginSession(accessToken: string | undefined): ActiveAuthSession | undefined {
  if (!accessToken || typeof getApp !== "function") return undefined;
  const current = currentAuthSession();
  if (!current || current.accessToken !== accessToken) return undefined;
  return { ...current };
}
