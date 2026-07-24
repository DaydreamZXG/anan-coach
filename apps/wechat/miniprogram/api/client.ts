import { environment } from "../config/environment";
import type { ApiEnvelope, ApiErrorPayload } from "../domain/contracts";

type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

export interface ApiRequestOptions {
  idempotencyKey?: string;
  timeoutMs?: number;
  taskEditLease?: { leaseId: string; generation: number };
}

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly statusCode: number,
    readonly requestId: string,
    readonly payload?: ApiErrorPayload,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

function createRequestId(): string {
  return `mp-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
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

          if (response.statusCode === 401) onUnauthorized?.();
          const payload: unknown = response.data;
          const publicError = isApiErrorPayload(payload) ? payload : undefined;
          reject(
            new ApiClientError(
              publicError?.message ?? "服务请求未成功。",
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
