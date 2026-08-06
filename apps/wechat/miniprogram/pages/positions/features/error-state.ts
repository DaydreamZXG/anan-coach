import { ApiClientError } from "../../../api/client";
import type { PositionOperationError } from "./contracts";

const needsProfileCodes = new Set([
  "onboarding_qualification_incomplete",
  "qualification_profile_missing",
]);
const noActiveReleaseCodes = new Set([
  "position_release_unavailable",
  "position_release_upcoming",
  "position_release_between_windows",
  "position_release_closed",
]);
const releaseTitles: Record<string, string> = {
  position_release_unavailable: "本年度岗位数据尚未发布",
  position_release_upcoming: "报名周期尚未开始",
  position_release_between_windows: "当前处于补录等待期",
  position_release_closed: "本年度报名已经结束",
};
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
      title: "岗位信息没有加载成功",
      message: "请返回岗位页重新进入；如果仍然失败，可联系岸岸客服。",
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
      title: releaseTitles[code] ?? "当前没有开放报名的岗位",
      message: error.message,
      requestId: error.requestId,
      retryable: false,
    };
  }
  if (code === "position_release_conflict") {
    return {
      status: "versionConflict",
      title: "岗位发布状态需要核验",
      message: error.message,
      requestId: error.requestId,
      retryable: true,
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
    title: "岗位信息没有加载成功",
    message: error.message,
    requestId: error.requestId,
    retryable: error.statusCode >= 500,
  };
}

export function shouldRetainPositionCommand(error: unknown): boolean {
  return !(error instanceof ApiClientError) || error.statusCode === 0 || error.statusCode >= 500;
}
