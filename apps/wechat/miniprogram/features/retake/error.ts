import { ApiClientError } from "../../api/client";

export interface RetakeError {
  message: string;
  requestId: string;
  code: string;
  retryable: boolean;
}

export function retakeError(error: unknown): RetakeError {
  if (!(error instanceof ApiClientError)) {
    return {
      message: "客户端没有识别到有效的服务错误，请携带页面信息联系支持。",
      requestId: "",
      code: "unknown_client_error",
      retryable: false,
    };
  }
  return {
    message: error.message,
    requestId: error.requestId,
    code: error.payload?.code ?? "request_failed",
    retryable: error.statusCode === 0 || error.statusCode >= 500,
  };
}
