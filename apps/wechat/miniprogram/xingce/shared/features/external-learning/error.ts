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
  payload?: unknown;
  details?: unknown;
}

export function externalLearningError(error: unknown): ExternalLearningError {
  const candidate = isRecord(error) ? (error as ErrorLike) : {};
  const payload = isRecord(candidate.payload) ? candidate.payload : {};
  const details = isRecord(candidate.details) ? candidate.details : payload;
  const status =
    typeof candidate.statusCode === "number"
      ? candidate.statusCode
      : typeof candidate.status === "number"
        ? candidate.status
        : null;
  const code = stringValue(details.code) || statusCode(status);
  const requestId = stringValue(details.requestId) || stringValue(candidate.requestId);
  const message =
    stringValue(details.message) || stringValue(candidate.message) || "请求未完成，请稍后重试。";
  if (isExternalProcessorBlocked(code, message)) {
    return {
      code,
      title: "外部 AI 处理器未放行",
      message:
        "当前不会把你的题图、答案或资料发送给未验证或非 HTTPS 的外部模型服务。请等待处理器审核通过，或先使用已确认题库、手动录入和私人资料的非 AI 处理能力。",
      requestId,
      retryable: false,
      uncertain: false,
    };
  }
  const uncertain = status === 0 || (status !== null && status >= 500);
  return {
    code,
    title: titleFor(code, status),
    message,
    requestId,
    retryable: uncertain || status === 408 || status === 429,
    uncertain,
  };
}

function titleFor(code: string, status: number | null): string {
  if (isExternalProcessorBlocked(code, "")) return "外部 AI 处理器未放行";
  if (code.includes("expired")) return "这次内容已过期";
  if (code.includes("quarantined") || code.includes("unsafe")) return "文件未通过安全检查";
  if (code.includes("scanner")) return "文件安全检查暂不可用";
  if (code.includes("conflict") || status === 409) return "内容刚刚有更新";
  if (status === 413 || code.includes("file_too_large")) return "文件超过限制";
  if (status === 415 || code.includes("unsupported")) return "文件格式不支持";
  if (status === 0) return "网络连接中断";
  return "操作没有完成";
}

function statusCode(status: number | null): string {
  if (status === 0) return "network_error";
  if (status === 409) return "version_conflict";
  if (status === 413) return "file_too_large";
  if (status === 415) return "unsupported_file_type";
  return status !== null && status > 0 ? `http_${String(status)}` : "unknown_error";
}

function isExternalProcessorBlocked(code: string, message: string): boolean {
  const normalized = `${code} ${message}`.toLowerCase();
  return (
    normalized.includes("processor_not_approved") ||
    normalized.includes("ai_answer_not_approved") ||
    normalized.includes("processor_configuration_invalid") ||
    normalized.includes("renderer_processing_not_approved") ||
    normalized.includes("renderer_host_not_approved") ||
    normalized.includes("recognition_route_not_approved") ||
    normalized.includes("answer_providers_not_approved") ||
    normalized.includes("release is not ready") ||
    normalized.includes("not release-approved") ||
    normalized.includes("not approved") ||
    normalized.includes("must use https") ||
    normalized.includes("plaintext_http_blocked")
  );
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
