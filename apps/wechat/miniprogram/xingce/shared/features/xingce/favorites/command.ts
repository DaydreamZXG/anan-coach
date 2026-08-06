import { ApiClientError } from "../../../../../api/client";
import type { QuestionFavoritesApi } from "../../../api/question-favorites";
import type {
  QuestionFavoriteState,
  SetQuestionFavoriteCommand,
} from "../../../../../api/generated/types.gen";
import { stableFingerprint } from "../../../../../platform/command-id";
import {
  OperationAttemptStore,
  type OperationAttemptRecord,
} from "../../../../../platform/operation-attempt-store";

const attemptLifetimeMs = 24 * 60 * 60 * 1_000;

export type QuestionFavoriteDesiredState = SetQuestionFavoriteCommand["desiredState"];

export interface QuestionFavoriteCanonicalInput {
  desiredState: QuestionFavoriteDesiredState;
  questionId: string;
  contentVersionId: string | null;
  sourceSessionId: string | null;
}

export interface ExecuteQuestionFavoriteInput {
  api: QuestionFavoritesApi;
  ownerUserId: string;
  questionId: string;
  desiredState: QuestionFavoriteDesiredState;
  contentVersionId?: string;
  sourceSessionId?: string;
  nowMs?: number;
}

export type QuestionFavoriteCommandOutcome =
  | {
      kind: "succeeded";
      favoriteState: QuestionFavoriteDesiredState;
      authoritativeState: QuestionFavoriteState | null;
      requestId: string;
    }
  | {
      kind: "unknown";
      favoriteState: QuestionFavoriteDesiredState | null;
      authoritativeState: QuestionFavoriteState | null;
      requestId: string;
      message: string;
    }
  | {
      kind: "terminal";
      requestId: string;
      message: string;
    };

export function questionFavoriteCanonicalInput(
  input: Omit<ExecuteQuestionFavoriteInput, "api" | "ownerUserId" | "nowMs">,
): QuestionFavoriteCanonicalInput {
  const questionId = input.questionId.trim();
  if (!questionId) throw new Error("收藏命令缺少题目编号。");
  if (input.desiredState === "favorited") {
    const contentVersionId = input.contentVersionId?.trim() ?? "";
    const sourceSessionId = input.sourceSessionId?.trim() ?? "";
    if (!contentVersionId || !sourceSessionId) {
      throw new Error("收藏题目必须绑定当前安全题目版本和作答会话。");
    }
    return {
      desiredState: input.desiredState,
      questionId,
      contentVersionId,
      sourceSessionId,
    };
  }
  return {
    desiredState: input.desiredState,
    questionId,
    contentVersionId: null,
    sourceSessionId: null,
  };
}

export function questionFavoriteAttemptScope(questionId: string): string {
  const normalized = questionId.trim();
  if (!normalized) throw new Error("收藏命令缺少题目编号。");
  return `xingce-question-favorite:${normalized}`;
}

export async function executeQuestionFavoriteCommand(
  input: ExecuteQuestionFavoriteInput,
): Promise<QuestionFavoriteCommandOutcome> {
  const nowMs = input.nowMs ?? Date.now();
  const canonicalInput = questionFavoriteCanonicalInput(input);
  const scope = questionFavoriteAttemptScope(canonicalInput.questionId);
  const store = new OperationAttemptStore(input.ownerUserId);
  cancelDriftedAttempt(store, scope, canonicalInput, nowMs);
  const attempt = await store.begin({
    scope,
    canonicalInput,
    idempotencyKeyPrefix: "xingce-favorite",
    expiresAtMs: nowMs + attemptLifetimeMs,
    nowMs,
  });
  const command: SetQuestionFavoriteCommand =
    canonicalInput.desiredState === "favorited"
      ? {
          desiredState: canonicalInput.desiredState,
          contentVersionId: canonicalInput.contentVersionId,
          sourceSessionId: canonicalInput.sourceSessionId,
        }
      : { desiredState: canonicalInput.desiredState };

  try {
    const response = await input.api.setState(
      canonicalInput.questionId,
      command,
      attempt.idempotencyKey,
    );
    settleAttempt(store, scope, attempt, "succeeded");
    return {
      kind: "succeeded",
      favoriteState: response.data.favoriteState,
      authoritativeState: null,
      requestId: response.requestId,
    };
  } catch (error) {
    if (isExplicitHttpFailure(error)) {
      settleAttempt(store, scope, attempt, "terminal");
      return {
        kind: "terminal",
        requestId: error.requestId,
        message: error.message,
      };
    }

    settleAttempt(store, scope, attempt, "unknown");
    return recoverUnknownOutcome(input.api, canonicalInput, attempt, store, scope, error);
  }
}

async function recoverUnknownOutcome(
  api: QuestionFavoritesApi,
  canonicalInput: QuestionFavoriteCanonicalInput,
  attempt: OperationAttemptRecord,
  store: OperationAttemptStore,
  scope: string,
  writeError: unknown,
): Promise<QuestionFavoriteCommandOutcome> {
  try {
    const response = await api.getState(canonicalInput.questionId);
    if (response.data.favoriteState === canonicalInput.desiredState) {
      settleAttempt(store, scope, attempt, "succeeded");
      return {
        kind: "succeeded",
        favoriteState: response.data.favoriteState,
        authoritativeState: response.data,
        requestId: response.requestId,
      };
    }
    return {
      kind: "unknown",
      favoriteState: response.data.favoriteState,
      authoritativeState: response.data,
      requestId: requestIdFrom(writeError) || response.requestId,
      message: "结果待确认；已读取服务端当前状态，重试会沿用刚才的请求。",
    };
  } catch (readError) {
    return {
      kind: "unknown",
      favoriteState: null,
      authoritativeState: null,
      requestId: requestIdFrom(writeError) || requestIdFrom(readError),
      message: "结果待确认；网络恢复后重试会沿用刚才的请求，不会反向切换。",
    };
  }
}

function cancelDriftedAttempt(
  store: OperationAttemptStore,
  scope: string,
  canonicalInput: QuestionFavoriteCanonicalInput,
  nowMs: number,
): void {
  const current = store.load(scope, nowMs);
  if (
    !current ||
    (current.state !== "pending" && current.state !== "unknown") ||
    current.canonicalFingerprint === stableFingerprint(canonicalInput)
  ) {
    return;
  }
  store.cancel(scope, current.idempotencyKey, nowMs);
}

function settleAttempt(
  store: OperationAttemptStore,
  scope: string,
  attempt: OperationAttemptRecord,
  outcome: "succeeded" | "terminal" | "unknown",
): void {
  const current = store.load(scope);
  if (
    !current ||
    current.idempotencyKey !== attempt.idempotencyKey ||
    (current.state !== "pending" && current.state !== "unknown")
  ) {
    return;
  }
  if (outcome === "succeeded") {
    store.markSucceeded(scope, attempt.idempotencyKey);
    return;
  }
  if (outcome === "terminal") {
    store.markTerminal(scope, attempt.idempotencyKey);
    return;
  }
  store.markTransportUnknown(scope, attempt.idempotencyKey);
}

function isExplicitHttpFailure(error: unknown): error is ApiClientError {
  return error instanceof ApiClientError && error.classification === "http_response";
}

function requestIdFrom(error: unknown): string {
  return error instanceof ApiClientError ? error.requestId : "";
}
