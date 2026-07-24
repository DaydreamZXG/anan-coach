import { ApiClientError } from "../../api/client";
import type { PositionOperationError } from "./contracts";

const needsProfileCodes = new Set([
  "onboarding_qualification_incomplete",
  "qualification_profile_missing",
]);
const noActiveReleaseCodes = new Set(["position_release_unavailable"]);
const versionConflictCodes = new Set([
  "qualification_profile_version_conflict",
  "onboarding_progress_version_conflict",
  "position_data_version_conflict",
  "position_version_conflict",
  "eligibility_snapshot_version_conflict",
  "primary_goal_preview_expired",
  "primary_goal_preview_consumed",
  "target_score_basis_expired",
  "revision_conflict",
]);
const notFoundCodes = new Set(["position_not_found", "not_found"]);

export function positionOperationError(error: unknown): PositionOperationError {
  if (!(error instanceof ApiClientError)) {
    return {
      status: "error",
      title: "岗位服务没有完成请求",
      message: "客户端收到无法识别的失败结果，请携带页面请求编号联系支持。",
      requestId: "",
      retryable: false,
    };
  }
  const code = error.payload?.code ?? "";
  if (error.statusCode === 0) {
    return {
      status: "network",
      title: "网络连接中断",
      message: "已加载的岗位不会被清空；恢复网络后可继续。",
      requestId: error.requestId,
      retryable: true,
    };
  }
  if (needsProfileCodes.has(code)) {
    return {
      status: "needsProfile",
      title: "先完成资格建档",
      message: error.message,
      requestId: error.requestId,
      retryable: false,
    };
  }
  if (noActiveReleaseCodes.has(code)) {
    return {
      status: "noActiveRelease",
      title: "本年度岗位尚未正式发布",
      message: error.message,
      requestId: error.requestId,
      retryable: false,
    };
  }
  if (versionConflictCodes.has(code)) {
    return {
      status: "versionConflict",
      title: "岗位依据已经更新",
      message: error.message,
      requestId: error.requestId,
      retryable: true,
    };
  }
  if (notFoundCodes.has(code) || error.statusCode === 404) {
    return {
      status: "notFound",
      title: "岗位不存在或已退出当前发布",
      message: error.message,
      requestId: error.requestId,
      retryable: false,
    };
  }
  return {
    status: "error",
    title: "岗位服务没有完成请求",
    message: error.message,
    requestId: error.requestId,
    retryable: error.statusCode >= 500,
  };
}

export function shouldRetainPositionCommand(error: unknown): boolean {
  return !(error instanceof ApiClientError) || error.statusCode === 0 || error.statusCode >= 500;
}
