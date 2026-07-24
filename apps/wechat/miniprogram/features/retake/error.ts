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
      message: "暂时无法判断失败原因。请重试；如果仍然失败，带上问题编号联系支持。",
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
