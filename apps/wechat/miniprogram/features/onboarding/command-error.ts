import { ApiClientError } from "../../api/client";

export interface OnboardingCommandError {
  message: string;
  requestId: string;
  code: string;
  statusCode: number;
  retryable: boolean;
}

export function onboardingCommandError(error: unknown): OnboardingCommandError {
  if (!(error instanceof ApiClientError)) {
    return {
      message: "暂时无法判断失败原因。请重试；如果仍然失败，带上问题编号联系支持。",
      requestId: "",
      code: "unknown_client_error",
      statusCode: 0,
      retryable: false,
    };
  }
  return {
    message: error.message,
    requestId: error.requestId,
    code: error.payload?.code ?? "request_failed",
    statusCode: error.statusCode,
    retryable: error.statusCode === 0 || error.statusCode >= 500,
  };
}

export function isOnboardingVersionConflict(error: OnboardingCommandError): boolean {
  return (
    error.statusCode === 409 &&
    [
      "onboarding_progress_version_conflict",
      "qualification_profile_version_conflict",
      "study_preference_version_conflict",
      "qualification_contract_conflict",
      "onboarding_already_completed",
    ].includes(error.code)
  );
}
