import type { ApiEnvelope } from "../domain/contracts";
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
  EssayLearningArtifactCommand,
  EssayLearningArtifactVersion,
  EssayReviewJobView,
  EssayReviewIntentView,
  EssayReviewQueueCommand,
  EssayReviewResponse,
  EssayTaskArtifactState,
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
} from "./generated/types.gen";
import type { ApiClient } from "./client";

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
};

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
    command: EssayDraftingCompletionCommand,
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
    command: EssayLearningArtifactCommand,
    idempotencyKey: string,
  ): Promise<ApiEnvelope<EssayLearningArtifactVersion>> {
    return this.client.post<EssayLearningArtifactVersion>(
      "/api/v2/essay-intelligence/artifacts",
      command,
      { idempotencyKey },
    );
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
