import type { ApiEnvelope } from "../../miniprogram/domain/contracts";
import type {
  EssayAnswerCreateCommand,
  EssayAnswerCreateResult,
  EssayAttemptCreateCommand,
  EssayAttemptCreateResult,
  EssayArtifactPlanTaskCompletionCommand,
  EssayArtifactPlanTaskCompletionResult,
  EssayCurrentReviewEnsureCommand,
  EssayCurrentReviewEnsureResult,
  EssayDraftingCompletionCommand,
  EssayDraftingCompletionResult,
  EssayFinding,
  EssayFindingDecision,
  EssayLearningArtifactCommand,
  EssayLearningArtifactVersion,
  EssayMasteryEvidenceRef,
  EssayMasteryProjection,
  EssayMasteryStateVersion,
  EssayReviewJobView,
  EssayReviewIntentView,
  EssayReviewQueueCommand,
  EssayReviewResponse,
  EssayRewriteCommand,
  EssayRewriteComparison,
  EssayRewriteFindingResolution,
  EssayRewritePlanTaskCompletionCommand,
  EssayRewritePlanTaskCompletionResult,
  EssayRewriteRecord,
  EssayRewriteResult,
  EssayTaskArtifactState,
  EssayTaskWorkspace,
  EssayTransferCommand,
  EssayTransferPlanTaskCompletionCommand,
  EssayTransferPlanTaskCompletionResult,
  EssayTransferRecord,
  EssayTransferResult,
  EssayWorkspaceMaterial,
  EssayWorkspaceMaterialSegment,
  EssayWorkspaceRubric,
  EssayWorkspaceTask,
  ExtractedPoint,
  MaterialAnnotationPayload,
  MaterialParagraphAnnotation,
  OutlinePayload,
  OutlineSection,
  PointExtractionPayload,
  ReflectionPayload,
  ReviewAcknowledgementPayload,
} from "../../miniprogram/api/generated/types.gen";
import type { ApiClient } from "../../miniprogram/api/client";

export type {
  EssayAnswerCreateCommand,
  EssayAnswerCreateResult,
  EssayAttemptCreateCommand,
  EssayAttemptCreateResult,
  EssayDraftingCompletionCommand,
  EssayDraftingCompletionResult,
  EssayCurrentReviewEnsureCommand,
  EssayCurrentReviewEnsureResult,
  EssayLearningArtifactCommand,
  EssayLearningArtifactVersion,
  EssayReviewJobView,
  EssayReviewIntentView,
  EssayReviewQueueCommand,
  EssayReviewResponse,
  EssayTaskWorkspace,
  EssayWorkspaceMaterial,
  EssayWorkspaceMaterialSegment,
  EssayWorkspaceRubric,
  EssayWorkspaceTask,
  ExtractedPoint,
  MaterialAnnotationPayload,
  MaterialParagraphAnnotation,
  OutlinePayload,
  OutlineSection,
  PointExtractionPayload,
  ReflectionPayload,
  ReviewAcknowledgementPayload,
  EssayFindingDecision,
  EssayMasteryEvidenceRef,
  EssayMasteryProjection,
  EssayMasteryStateVersion,
  EssayRewriteFindingResolution,
};

export type EssayReflectionPayloadV2 = ReflectionPayload;
export type EssayDraftingCompletionCommandV2 = EssayDraftingCompletionCommand;
export type EssayFindingDecisionV1 = EssayFindingDecision;
export type EssayFindingDecisionPayloadV1 = ReviewAcknowledgementPayload;

export type EssayFindingDecisionArtifactCommand = Omit<
  EssayLearningArtifactCommand,
  "artifactType" | "payload"
> & {
  artifactType: "review_acknowledgement";
  payload: EssayFindingDecisionPayloadV1;
};

export type EssayRewriteCommandV2 = EssayRewriteCommand;
export type EssayRewriteRecordV2 = EssayRewriteRecord;
export type EssayRewriteResultV2 = EssayRewriteResult;
export type EssayRewriteComparisonV1 = EssayRewriteComparison;
export type EssayTransferCommandV2 = EssayTransferCommand;
export type EssayTransferRecordV2 = EssayTransferRecord;
export type EssayTransferResultV2 = EssayTransferResult;
export type EssayRewritePlanTaskCompletionCommandV2 = EssayRewritePlanTaskCompletionCommand;
export type EssayRewritePlanTaskCompletionResultV2 = EssayRewritePlanTaskCompletionResult;
export type EssayTransferPlanTaskCompletionCommandV2 = EssayTransferPlanTaskCompletionCommand;
export type EssayTransferPlanTaskCompletionResultV2 = EssayTransferPlanTaskCompletionResult;

export type EssayArtifactType = EssayLearningArtifactCommand["artifactType"];
export type EssayPointCategory = ExtractedPoint["category"];
export type EssayArtifactPayload = EssayLearningArtifactCommand["payload"];
export type EssayArtifactPlanTaskCommand = EssayArtifactPlanTaskCompletionCommand;
export type EssayArtifactPlanTaskResult = EssayArtifactPlanTaskCompletionResult;
export type EssayWorkspaceFinding = EssayFinding;

export class EssayIntelligenceApi {
  constructor(private readonly client: ApiClient) {}

  getWorkspace(taskVersionId: string): Promise<ApiEnvelope<EssayTaskWorkspace>> {
    return this.client.get<EssayTaskWorkspace>(
      `/api/v2/essay-intelligence/tasks/${encodeURIComponent(taskVersionId)}/workspace`,
    );
  }

  getCurrentArtifacts(taskVersionId: string): Promise<ApiEnvelope<EssayTaskArtifactState>> {
    return this.client.get<EssayTaskArtifactState>(
      `/api/v2/essay-intelligence/tasks/${encodeURIComponent(taskVersionId)}/artifacts/current`,
    );
  }

  createAttempt(
    command: EssayAttemptCreateCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayAttemptCreateResult>> {
    return this.client.post<EssayAttemptCreateResult>(
      "/api/v2/essay-intelligence/attempts",
      command,
      { idempotencyKey },
    );
  }

  createAnswer(
    command: EssayAnswerCreateCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayAnswerCreateResult>> {
    return this.client.post<EssayAnswerCreateResult>(
      "/api/v2/essay-intelligence/answers",
      command,
      { idempotencyKey },
    );
  }

  completeDraftingPlanTask(
    command: EssayDraftingCompletionCommandV2,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayDraftingCompletionResult>> {
    return this.client.post<EssayDraftingCompletionResult>(
      "/api/v2/essay-intelligence/drafting/complete-plan-task",
      command,
      { idempotencyKey },
    );
  }

  queueGroundedReview(
    command: EssayReviewQueueCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayReviewJobView>> {
    return this.client.post<EssayReviewJobView>("/api/v2/essay-intelligence/reviews", command, {
      idempotencyKey,
    });
  }

  ensureCurrentGroundedReview(
    command: EssayCurrentReviewEnsureCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayCurrentReviewEnsureResult>> {
    return this.client.post<EssayCurrentReviewEnsureResult>(
      "/api/v2/essay-intelligence/reviews/current/ensure",
      command,
      { idempotencyKey },
    );
  }

  getGroundedReviewJob(jobId: string): Promise<ApiEnvelope<EssayReviewJobView>> {
    return this.client.get<EssayReviewJobView>(
      `/api/v2/essay-intelligence/review-jobs/${encodeURIComponent(jobId)}`,
    );
  }

  regenerateGroundedReview(
    sourceJobId: string,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayReviewJobView>> {
    return this.client.post<EssayReviewJobView>(
      `/api/v2/essay-intelligence/review-jobs/${encodeURIComponent(sourceJobId)}/regenerate`,
      {},
      { idempotencyKey },
    );
  }

  getGroundedReview(reviewVersionId: string): Promise<ApiEnvelope<EssayReviewResponse>> {
    return this.client.get<EssayReviewResponse>(
      `/api/v2/essay-intelligence/reviews/${encodeURIComponent(reviewVersionId)}`,
    );
  }

  createArtifact(
    command: EssayLearningArtifactCommand | EssayFindingDecisionArtifactCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayLearningArtifactVersion>> {
    return this.client.post<EssayLearningArtifactVersion>(
      "/api/v2/essay-intelligence/artifacts",
      command,
      { idempotencyKey },
    );
  }

  createRewrite(
    command: EssayRewriteCommandV2,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayRewriteResultV2>> {
    return this.client.post<EssayRewriteResultV2>("/api/v2/essay-intelligence/rewrites", command, {
      idempotencyKey,
    });
  }

  getRewriteComparison(rewriteVersionId: string): Promise<ApiEnvelope<EssayRewriteComparisonV1>> {
    return this.client.get<EssayRewriteComparisonV1>(
      `/api/v2/essay-intelligence/rewrites/${encodeURIComponent(rewriteVersionId)}/comparison`,
    );
  }

  completeRewritePlanTask(
    command: EssayRewritePlanTaskCompletionCommandV2,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayRewritePlanTaskCompletionResultV2>> {
    return this.client.post<EssayRewritePlanTaskCompletionResultV2>(
      "/api/v2/essay-intelligence/rewrites/complete-plan-task",
      command,
      { idempotencyKey },
    );
  }

  createTransfer(
    command: EssayTransferCommandV2,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayTransferResultV2>> {
    return this.client.post<EssayTransferResultV2>(
      "/api/v2/essay-intelligence/transfers",
      command,
      { idempotencyKey },
    );
  }

  completeTransferPlanTask(
    command: EssayTransferPlanTaskCompletionCommandV2,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayTransferPlanTaskCompletionResultV2>> {
    return this.client.post<EssayTransferPlanTaskCompletionResultV2>(
      "/api/v2/essay-intelligence/transfers/complete-plan-task",
      command,
      { idempotencyKey },
    );
  }

  getMastery(): Promise<ApiEnvelope<EssayMasteryProjection>> {
    return this.client.get<EssayMasteryProjection>("/api/v2/essay-intelligence/mastery");
  }

  completePlanTask(
    command: EssayArtifactPlanTaskCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayArtifactPlanTaskResult>> {
    return this.client.post<EssayArtifactPlanTaskResult>(
      "/api/v2/essay-intelligence/artifacts/complete-plan-task",
      command,
      { idempotencyKey },
    );
  }
}
