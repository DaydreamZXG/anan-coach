export interface ExternalLearningError {
  code: string;
  title: string;
  message: string;
  requestId: string;
  retryable: boolean;
  uncertain: boolean;
}

interface ErrorLike {
  message?: unknown;
  status?: unknown;
  statusCode?: unknown;
  requestId?: unknown;
  details?: unknown;
}

export function externalLearningError(error: unknown): ExternalLearningError {
  const candidate = isRecord(error) ? (error as ErrorLike) : {};
  const details = isRecord(candidate.details) ? candidate.details : {};
  const status =
    typeof candidate.statusCode === "number"
      ? candidate.statusCode
      : typeof candidate.status === "number"
        ? candidate.status
        : 0;
  const code = stringValue(details.code) || statusCode(status);
  const requestId = stringValue(details.requestId) || stringValue(candidate.requestId);
  const message =
    stringValue(details.message) || stringValue(candidate.message) || "请求未完成，请稍后重试。";
  const uncertain = status === 0 || status >= 500;
  return {
    code,
    title: titleFor(code, status),
    message,
    requestId,
    retryable: uncertain || status === 408 || status === 429,
    uncertain,
  };
}

function titleFor(code: string, status: number): string {
  if (code.includes("expired")) return "当前版本已过期";
  if (code.includes("quarantined") || code.includes("unsafe")) return "文件未通过安全检查";
  if (code.includes("conflict") || status === 409) return "版本已经更新";
  if (status === 413 || code.includes("file_too_large")) return "文件超过限制";
  if (status === 415 || code.includes("unsupported")) return "文件格式不支持";
  if (status === 0) return "网络连接中断";
  return "操作没有完成";
}

function statusCode(status: number): string {
  if (status === 0) return "network_error";
  if (status === 409) return "version_conflict";
  if (status === 413) return "file_too_large";
  if (status === 415) return "unsupported_file_type";
  return status > 0 ? `http_${String(status)}` : "unknown_error";
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
