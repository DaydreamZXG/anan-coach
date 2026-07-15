import type { TodayDashboard } from "./generated/types.gen";
import type { ApiClient } from "./client";
import type { ApiEnvelope } from "../domain/contracts";

export type XingceTaskMode = "practice" | "targeted" | "baseline" | "verification";
export type XingceConfidence = "low" | "medium" | "high";
export type XingceSessionState =
  "answering" | "submitted" | "reflection_required" | "cause_decision_required" | "completed";
export type XingceResultCode =
  "correct" | "incorrect" | "timeout" | "guessed_correct" | "skipped" | "mixed";
export type XingceCauseDecision = "replace" | "skip";

export interface XingceMaterialPosition {
  anchorId: string;
  scrollTop: number;
  zoom: number;
}

export interface XingceQuestionAsset {
  assetId: string;
  url: string;
  altText: string;
}

export interface XingceLearnerMaterial {
  materialId: string;
  title: string | null;
  text: string | null;
  html: string | null;
  assets: XingceQuestionAsset[];
}

export interface XingceLearnerOption {
  optionId: string;
  label: string;
  text: string;
  html: string | null;
}

/** Learner-safe question projection. Answers and explanations intentionally do not exist here. */
export interface XingceLearnerQuestion {
  questionId: string;
  contentVersionId: string;
  materialId: string | null;
  moduleName: string;
  stem: string;
  stemHtml: string | null;
  options: XingceLearnerOption[];
}

export interface XingceLearnerBundle {
  bundleVersionId: string;
  bundleType: "single" | "shared_material";
  materials: XingceLearnerMaterial[];
  questions: XingceLearnerQuestion[];
}

export interface XingceAnswerCheckpoint {
  questionId: string;
  selectedOptionIds: string[];
  eliminatedOptionIds: string[];
  confidence: XingceConfidence | null;
  marked: boolean;
  skipped: boolean;
  elapsedSeconds: number;
  draftText: string;
  materialPosition?: XingceMaterialPosition;
}

export interface XingceCheckpointCommand {
  expectedRevision: number;
  currentQuestionId: string;
  answers: XingceAnswerCheckpoint[];
  totalElapsedSeconds: number;
}

export interface XingceSubmitCommand {
  expectedRevision: number;
  totalElapsedSeconds: number;
}

export interface XingceResultItem {
  questionId: string;
  resultCode: Exclude<XingceResultCode, "mixed">;
  selectedOptionIds: string[];
  correctOptionIds: string[];
  explanation: string | null;
  explanationSource: string | null;
}

export interface XingceAttemptResult {
  attemptId: string;
  resultCode: XingceResultCode;
  correctCount: number;
  totalCount: number;
  items: XingceResultItem[];
}

export interface XingceReflectionVersion {
  reflectionVersionId: string;
  rawText: string;
  tags: string[];
  strategyNote: string | null;
  doubtNote: string | null;
  versionNo: number;
}

export interface XingceCauseDecisionVersion {
  decisionVersionId: string;
  decision: XingceCauseDecision;
  replacementCause: string | null;
  versionNo: number;
}

export interface XingceAttemptReview {
  reviewId: string;
  revision: number;
  state:
    | "reflection_required"
    | "cause_candidate_pending"
    | "cause_decision_required"
    | "immediate_verification_due";
  aiCandidate: {
    state: "unavailable";
    reason: string;
    charged: false;
  };
  reflection: XingceReflectionVersion | null;
  causeDecision: XingceCauseDecisionVersion | null;
}

export interface XingceCompletionReceipt {
  receiptId: string;
  settlementStatus: "completed" | "late_evidence_linked";
  plan: TodayDashboard;
}

export interface XingceTaskSession {
  sessionId: string;
  planTaskId: string;
  revision: number;
  state: XingceSessionState;
  mode: XingceTaskMode;
  bundle: XingceLearnerBundle;
  currentQuestionId: string;
  answers: XingceAnswerCheckpoint[];
  totalElapsedSeconds: number;
  result: XingceAttemptResult | null;
  review: XingceAttemptReview | null;
  completion: XingceCompletionReceipt | null;
}

export interface XingceReflectionCommand {
  expectedRevision: number;
  rawText: string;
  tags: string[];
  strategyNote?: string;
  doubtNote?: string;
}

export interface XingceCauseDecisionCommand {
  expectedRevision: number;
  decision: XingceCauseDecision;
  replacementCause?: string;
}

export class XingceLearningApi {
  constructor(private readonly client: ApiClient) {}

  startSession(
    planTaskId: string,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.post<XingceTaskSession>(
      "/api/v2/xingce/task-sessions",
      { planTaskId },
      { idempotencyKey },
    );
  }

  getSession(sessionId: string): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.get<XingceTaskSession>(sessionPath(sessionId));
  }

  saveCheckpoint(
    sessionId: string,
    command: XingceCheckpointCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.put<XingceTaskSession>(`${sessionPath(sessionId)}/checkpoint`, command, {
      idempotencyKey,
    });
  }

  submitAttempt(
    sessionId: string,
    command: XingceSubmitCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.post<XingceTaskSession>(`${sessionPath(sessionId)}/submit`, command, {
      idempotencyKey,
    });
  }

  submitReflection(
    reviewId: string,
    command: XingceReflectionCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.put<XingceTaskSession>(`${reviewPath(reviewId)}/reflection`, command, {
      idempotencyKey,
    });
  }

  decideCause(
    reviewId: string,
    command: XingceCauseDecisionCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.put<XingceTaskSession>(`${reviewPath(reviewId)}/cause-decision`, command, {
      idempotencyKey,
    });
  }
}

function sessionPath(sessionId: string): string {
  return `/api/v2/xingce/task-sessions/${encodeURIComponent(requiredId(sessionId, "sessionId"))}`;
}

function reviewPath(reviewId: string): string {
  return `/api/v2/xingce/attempt-reviews/${encodeURIComponent(requiredId(reviewId, "reviewId"))}`;
}

function requiredId(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Xingce API requires ${label}`);
  return normalized;
}
