import { config } from "../config/env";

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string;
  readonly body: unknown;

  constructor(status: number, requestId: string, body: unknown) {
    super(`API ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
    this.body = body;
  }
}

export type UnauthorizedHandler = () => void;

let onUnauthorized: UnauthorizedHandler | null = null;

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  onUnauthorized = handler;
}

function newRequestId(): string {
  return `mp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

export interface RequestOptions {
  method: "GET" | "POST" | "PUT" | "DELETE";
  path: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
  token?: string | null;
  /** 额外请求头（如 Idempotency-Key） */
  extraHeaders?: Record<string, string>;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const base = `${config.apiBaseUrl}${path}`;
  if (!query) return base;
  const parts = Object.entries(query)
    .filter(([, v]) => v !== undefined)
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
  return parts.length ? `${base}?${parts.join("&")}` : base;
}

/**
 * 统一请求层：注入鉴权、request ID，归一化错误。
 * 401 时通过 setUnauthorizedHandler 注册的回调清理会话，由调用方决定重登。
 */
export function request<T>(options: RequestOptions): Promise<T> {
  const requestId = newRequestId();
  const header: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Request-Id": requestId,
    ...options.extraHeaders,
  };
  if (options.token) {
    header.Authorization = `Bearer ${options.token}`;
  }

  return new Promise<T>((resolvePromise, rejectPromise) => {
    const config_: WechatMiniprogram.RequestOption = {
      url: buildUrl(options.path, options.query),
      method: options.method,
      header,
      success: (res) => {
        const status = res.statusCode;
        if (status >= 200 && status < 300) {
          resolvePromise(res.data as T);
          return;
        }
        if (status === 401 && onUnauthorized) {
          onUnauthorized();
        }
        rejectPromise(new ApiError(status, requestId, res.data));
      },
      fail: (err) => {
        rejectPromise(new ApiError(-1, requestId, err.errMsg));
      },
    };
    if (options.body !== undefined) {
      config_.data = options.body as WechatMiniprogram.IAnyObject;
    }
    wx.request(config_);
  });
}
