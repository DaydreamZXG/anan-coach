import type {
  AiCauseCandidateState as GeneratedAiCauseCandidateState,
  AnnotationStroke as GeneratedAnnotationStroke,
  AttemptActionEvent as GeneratedAttemptActionEvent,
  AttemptReviewProjection as GeneratedAttemptReviewProjection,
  CauseDecisionCommand as GeneratedCauseDecisionCommand,
  CauseDecisionVersionProjection as GeneratedCauseDecisionVersionProjection,
  CauseFollowupCommand as GeneratedCauseFollowupCommand,
  ItemInterpretation as GeneratedItemInterpretation,
  MasteryQueueAction as GeneratedMasteryQueueAction,
  MasteryQueueItem as GeneratedMasteryQueueItem,
  MasteryQueuePage as GeneratedMasteryQueuePage,
  MasteryQueueSummary as GeneratedMasteryQueueSummary,
  QuestionCauseCandidateProjection as GeneratedQuestionCauseCandidateProjection,
  QuestionDisputeReceipt as GeneratedQuestionDisputeReceipt,
  ReflectionCommand as GeneratedReflectionCommand,
  ReflectionVersionProjection as GeneratedReflectionVersionProjection,
  ReportQuestionDisputeCommand as GeneratedReportQuestionDisputeCommand,
  RescueMethodCard as GeneratedRescueMethodCard,
  RescueNodeContext as GeneratedRescueNodeContext,
  RescueSimilarOption as GeneratedRescueSimilarOption,
  RescueSimilarQuestion as GeneratedRescueSimilarQuestion,
  VerificationOutcomeProjection as GeneratedVerificationOutcomeProjection,
  XingceAttemptResult as GeneratedXingceAttemptResult,
  XingceCompletionReceipt as GeneratedXingceCompletionReceipt,
  XingceGroupSummary as GeneratedXingceGroupSummary,
  XingceRescue as GeneratedXingceRescue,
  XingceRescueItem as GeneratedXingceRescueItem,
  XingceResultItem as GeneratedXingceResultItem,
  XingceTaskBriefing as GeneratedXingceTaskBriefing,
  XingceTimingEvidence as GeneratedXingceTimingEvidence,
} from "../../miniprogram/api/generated/types.gen";
import type { ApiClient } from "../../miniprogram/api/client";
import type { ApiEnvelope } from "../../miniprogram/domain/contracts";
import type { TaskEditLeaseToken } from "./task-editing";
import type { ContentAnchor, RichContentBlock } from "../task-runtime/content-renderer/model";

export type XingceTaskMode = "practice" | "targeted" | "baseline" | "verification";
export type XingceConfidence = "low" | "medium" | "high";
export type XingceSessionState =
  | "answering"
  | "submitted"
  | "reflection_required"
  | "cause_candidate_processing"
  | "cause_followup_required"
  | "cause_decision_required"
  | "cause_evidence_insufficient"
  | "cause_agent_terminated"
  | "completed";
export type XingceResultCode = GeneratedXingceAttemptResult["resultCode"];
export type XingceCauseDecision = GeneratedCauseDecisionCommand["decision"];
export type XingceObjectiveVerdict = NonNullable<GeneratedXingceResultItem["objectiveVerdict"]>;
export type XingceDisposition = NonNullable<GeneratedXingceResultItem["disposition"]>;
export type XingceConfidenceEvidence = NonNullable<GeneratedXingceResultItem["confidenceEvidence"]>;
export type XingceItemInterpretationCode = "method_correct_calculation_error";

export interface XingceMaterialPosition {
  anchorId: string;
  scrollTop: number;
  zoom: number;
}

export interface XingceQuestionAsset {
  assetId: string;
  url: string;
  altText: string;
  originalOcrUrl: string;
  contentKind: "image" | "graph";
}

export interface XingceLearnerMaterial {
  materialId: string;
  title: string | null;
  text: string | null;
  html: string | null;
  assets: XingceQuestionAsset[];
  content: RichContentBlock[];
  anchors: ContentAnchor[];
}

export interface XingceLearnerOption {
  optionId: string;
  label: string;
  text: string;
  html: string | null;
  content: RichContentBlock[];
}

/** Learner-safe question projection. Answers and explanations intentionally do not exist here. */
export interface XingceLearnerQuestion {
  questionId: string;
  contentVersionId: string;
  materialId: string | null;
  moduleName: string;
  stem: string;
  stemHtml: string | null;
  stemContent: RichContentBlock[];
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
  /** Figma 70:2 题面批注与 75:2/78:2 动作事实（对应后端 AnswerCheckpoint 扩展）。 */
  annotations?: GeneratedAnnotationStroke[];
  actionEvents?: GeneratedAttemptActionEvent[];
  /** 契约增量 v1：动作事件达到 400 上限被截断时置 true；缺省等价 false。 */
  actionEventsTruncated?: boolean;
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

export type XingceResultItem = GeneratedXingceResultItem;
export type XingceTimingEvidence = GeneratedXingceTimingEvidence;
export type XingceGroupSummary = GeneratedXingceGroupSummary;
export type XingceAttemptResult = GeneratedXingceAttemptResult;

export type XingceItemInterpretation = Omit<GeneratedItemInterpretation, "interpretation"> & {
  interpretation: XingceItemInterpretationCode;
};

export type XingceReflectionVersion = Omit<
  GeneratedReflectionVersionProjection,
  "itemInterpretations"
> & {
  itemInterpretations?: XingceItemInterpretation[];
};

export type XingceCauseDecisionVersion = GeneratedCauseDecisionVersionProjection;
export type XingceCauseCandidate = GeneratedAiCauseCandidateState;
/** 契约增量：逐题候选错因（纯展示；证据不足的题显式标注，不给 causeCode）。 */
export type XingceQuestionCauseCandidate = GeneratedQuestionCauseCandidateProjection;
export type XingceAttemptReview = Omit<
  GeneratedAttemptReviewProjection,
  "aiCandidate" | "reflection" | "causeDecision"
> & {
  aiCandidate: XingceCauseCandidate;
  reflection?: XingceReflectionVersion | null;
  causeDecision?: XingceCauseDecisionVersion | null;
};
export type XingceCompletionReceipt = GeneratedXingceCompletionReceipt;

/** 错题即时救场（只读，做完一组题确认错因后返回）。 */
export type XingceRescueMethodCard = GeneratedRescueMethodCard;
export type XingceRescueSimilarOption = GeneratedRescueSimilarOption;
export type XingceRescueSimilarQuestion = GeneratedRescueSimilarQuestion;
export type XingceRescueNodeContext = GeneratedRescueNodeContext;
export type XingceRescueItem = GeneratedXingceRescueItem;
export type XingceRescue = GeneratedXingceRescue;

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
  verificationOutcome: XingceVerificationOutcome | null;
  review: XingceAttemptReview | null;
  completion: XingceCompletionReceipt | null;
}

export type XingceReflectionCommand = Omit<GeneratedReflectionCommand, "itemInterpretations"> & {
  itemInterpretations?: XingceItemInterpretation[];
};

export type XingceVerificationOutcome = GeneratedVerificationOutcomeProjection;

export type XingceCauseDecisionCommand = Omit<
  GeneratedCauseDecisionCommand,
  "candidateVersionId" | "decision"
> & {
  candidateVersionId: string;
  decision: Exclude<XingceCauseDecision, "skip">;
};

export type XingceCauseFollowupCommand = GeneratedCauseFollowupCommand;

export type XingceQuestionDisputeCategory = GeneratedReportQuestionDisputeCommand["category"];

export type XingceQuestionDisputeCommand = GeneratedReportQuestionDisputeCommand;
export type XingceQuestionDisputeReceipt = GeneratedQuestionDisputeReceipt;

export type XingceBriefingMode = GeneratedXingceTaskBriefing["mode"];
export type XingceBriefingMetric =
  GeneratedXingceTaskBriefing["completion"]["requiredMetrics"][number];
export type XingceTaskBriefing = GeneratedXingceTaskBriefing;

export type XingceMasteryQueueBucket = GeneratedMasteryQueuePage["bucket"];
export type XingceMasteryQueueActionState = GeneratedMasteryQueueAction["state"];
export type XingceMasteryQueueSummary = GeneratedMasteryQueueSummary;
export type XingceMasteryQueueAction = GeneratedMasteryQueueAction;
export type XingceMasteryQueueItem = GeneratedMasteryQueueItem;
export type XingceMasteryQueuePage = GeneratedMasteryQueuePage;

export interface XingceMasteryQueueQuery {
  bucket: XingceMasteryQueueBucket;
  cursor?: string;
  pageSize?: number;
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

  getTaskBriefing(planTaskId: string): Promise<ApiEnvelope<XingceTaskBriefing>> {
    return this.client.get<XingceTaskBriefing>(
      `/api/v2/xingce/plan-tasks/${encodeURIComponent(requiredId(planTaskId, "planTaskId"))}/briefing`,
    );
  }

  getSession(sessionId: string): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.get<XingceTaskSession>(sessionPath(sessionId));
  }

  saveCheckpoint(
    sessionId: string,
    command: XingceCheckpointCommand,
    idempotencyKey: string,
    lease: TaskEditLeaseToken,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.put<XingceTaskSession>(`${sessionPath(sessionId)}/checkpoint`, command, {
      idempotencyKey,
      taskEditLease: lease,
    });
  }

  submitAttempt(
    sessionId: string,
    command: XingceSubmitCommand,
    idempotencyKey: string,
    lease: TaskEditLeaseToken,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.post<XingceTaskSession>(`${sessionPath(sessionId)}/submit`, command, {
      idempotencyKey,
      taskEditLease: lease,
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

  submitCauseFollowup(
    reviewId: string,
    command: XingceCauseFollowupCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceTaskSession>> {
    return this.client.put<XingceTaskSession>(`${reviewPath(reviewId)}/cause-followup`, command, {
      idempotencyKey,
    });
  }

  /** 错题即时救场：错因 -> 方法卡 -> 同类题（只读，不打断做题）。 */
  getRescue(reviewId: string): Promise<ApiEnvelope<XingceRescue>> {
    return this.client.get<XingceRescue>(`${reviewPath(reviewId)}/rescue`);
  }

  reportDispute(
    sessionId: string,
    command: XingceQuestionDisputeCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<XingceQuestionDisputeReceipt>> {
    return this.client.post<XingceQuestionDisputeReceipt>(
      `${sessionPath(sessionId)}/disputes`,
      command,
      { idempotencyKey },
    );
  }

  getMasteryQueue(query: XingceMasteryQueueQuery): Promise<ApiEnvelope<XingceMasteryQueuePage>> {
    const pageSize = query.pageSize ?? 30;
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 100) {
      throw new Error("Xingce mastery queue pageSize must be an integer between 1 and 100");
    }
    const params = [`bucket=${encodeURIComponent(query.bucket)}`, `pageSize=${String(pageSize)}`];
    if (query.cursor !== undefined) {
      const cursor = query.cursor.trim();
      if (!cursor) throw new Error("Xingce mastery queue cursor must not be empty");
      params.push(`cursor=${encodeURIComponent(cursor)}`);
    }
    return this.client.get<XingceMasteryQueuePage>(
      `/api/v2/xingce/mastery-queue?${params.join("&")}`,
    );
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
