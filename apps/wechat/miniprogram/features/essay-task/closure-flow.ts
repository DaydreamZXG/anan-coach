import type {
  EssayIntelligenceApi,
  EssayReviewJobView,
  EssayReviewResponse,
  EssayTaskWorkspace,
  EssayTransferPlanTaskCompletionResultV2,
} from "../../api/essay-intelligence";
import type { EssayTransferWriterContext } from "./capabilities";
import {
  buildTransferAnswerCommand,
  buildTransferAttemptCommand,
  buildTransferCommand,
  buildTransferDecisionArtifact,
  buildTransferPlanTaskCompletionCommand,
  criterionOptions,
  decisionsForReview,
  EssayClosureError,
  type CriterionOption,
  type EssayTransferDraft,
} from "./closure";

export interface EssayTransferFlowContext {
  api: EssayIntelligenceApi;
  workspace: EssayTaskWorkspace;
  writerContext: EssayTransferWriterContext;
  draft: EssayTransferDraft;
  planTaskId: string;
  basedOnExecutionVersionId?: string;
  elapsedSeconds: number;
  allowedCriterionCodes: readonly string[];
  createIdempotencyKey(scope: string): Promise<string>;
  persist(draft: EssayTransferDraft): void;
  isActive(): boolean;
}

export type EssayTransferFlowResult =
  | { state: "stale"; draft: EssayTransferDraft }
  | { state: "awaiting_review"; draft: EssayTransferDraft }
  | {
      state: "completed";
      draft: EssayTransferDraft;
      result: EssayTransferPlanTaskCompletionResultV2;
    };

/**
 * Advances the durable transfer state machine until it must await AI review or
 * reaches the atomic writer + plan-completion terminal. Every remote command is
 * persisted with its idempotency key before the request leaves the device.
 */
export async function advanceEssayTransferFlow(
  context: EssayTransferFlowContext,
): Promise<EssayTransferFlowResult> {
  let draft = context.draft;
  if (draft.phase === "drafting") {
    const key =
      draft.attemptIdempotencyKey || (await context.createIdempotencyKey("essay-transfer-attempt"));
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, { ...draft, attemptIdempotencyKey: key });
    const result = (
      await context.api.createAttempt(
        buildTransferAttemptCommand(context.workspace, draft, context.elapsedSeconds),
        key,
      )
    ).data;
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, {
      ...draft,
      phase: "attempt_saved",
      attemptId: result.attempt.attemptId,
    });
  }

  if (draft.phase === "attempt_saved") {
    const key =
      draft.answerIdempotencyKey || (await context.createIdempotencyKey("essay-transfer-answer"));
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, { ...draft, answerIdempotencyKey: key });
    const result = (
      await context.api.createAnswer(buildTransferAnswerCommand(draft.attemptId, draft), key)
    ).data;
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, {
      ...draft,
      phase: "answer_saved",
      answerVersionId: result.answer.answerVersionId,
    });
  }

  if (draft.phase === "answer_saved") {
    const key =
      draft.reviewIdempotencyKey || (await context.createIdempotencyKey("essay-transfer-review"));
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, { ...draft, reviewIdempotencyKey: key });
    const job = (
      await context.api.queueGroundedReview(
        {
          attemptId: draft.attemptId,
          answerVersionId: draft.answerVersionId,
          reviewScope: "grounded_full",
        },
        key,
      )
    ).data;
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, { ...draft, phase: "review_queued", reviewJobId: job.jobId });
  }

  if (draft.phase === "review_queued") {
    return { state: "awaiting_review", draft };
  }

  if (draft.phase === "review_ready") {
    const reviewVersionId = draft.reviewVersionId;
    if (!reviewVersionId) throw new EssayClosureError("迁移评阅版本缺失。");
    const review = (await context.api.getGroundedReview(reviewVersionId)).data;
    if (!context.isActive()) return { state: "stale", draft };
    const key =
      draft.decisionIdempotencyKey ||
      (await context.createIdempotencyKey("essay-transfer-decision"));
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, { ...draft, decisionIdempotencyKey: key });
    const artifact = (
      await context.api.createArtifact(
        buildTransferDecisionArtifact(context.workspace, review, draft),
        key,
      )
    ).data;
    if (!context.isActive()) return { state: "stale", draft };
    draft = persist(context, {
      ...draft,
      phase: "decision_saved",
      findingDecisionArtifactVersionId: artifact.artifactVersionId,
    });
  }

  if (draft.phase === "decision_saved") {
    let atomicCommand = draft.atomicCommand;
    let atomicIdempotencyKey = draft.atomicIdempotencyKey;
    if (!atomicCommand || !atomicIdempotencyKey) {
      atomicCommand = buildTransferPlanTaskCompletionCommand(
        buildTransferCommand(context.writerContext, draft, context.allowedCriterionCodes),
        context.planTaskId,
        Math.max(1, Math.ceil(context.elapsedSeconds / 60)),
        context.basedOnExecutionVersionId,
      );
      atomicIdempotencyKey = await context.createIdempotencyKey(
        "essay-transfer-complete-plan-task",
      );
      if (!context.isActive()) return { state: "stale", draft };
      draft = persist(context, { ...draft, atomicCommand, atomicIdempotencyKey });
    }
    const result = (await context.api.completeTransferPlanTask(atomicCommand, atomicIdempotencyKey))
      .data;
    if (!context.isActive()) return { state: "stale", draft };
    return { state: "completed", draft, result };
  }

  if (draft.phase === "completed") {
    throw new EssayClosureError("迁移验证已完成，不能再执行新 writer。");
  }
  if (draft.phase === "context_invalid") {
    throw new EssayClosureError("迁移任务上下文已失效，必须从最新今日计划重新进入。");
  }
  throw new EssayClosureError("迁移恢复状态不可执行。");
}

export type EssayTransferReviewPollResult =
  | { state: "pending"; job: EssayReviewJobView }
  | {
      state: "ready";
      review: EssayReviewResponse;
      draft: EssayTransferDraft;
      criterionOptions: CriterionOption[];
    };

export async function pollEssayTransferReview(
  api: EssayIntelligenceApi,
  draft: EssayTransferDraft,
  sourceReview: EssayReviewResponse,
): Promise<EssayTransferReviewPollResult> {
  if (draft.phase !== "review_queued" || !draft.reviewJobId) {
    throw new EssayClosureError("迁移评阅轮询缺少已持久化的 job。");
  }
  const job = (await api.getGroundedReviewJob(draft.reviewJobId)).data;
  if (job.status === "queued" || job.status === "running") {
    return { state: "pending", job };
  }
  if (job.status !== "succeeded" || job.reviewStatus !== "completed") {
    throw new EssayClosureError("新题评阅未形成完整证据，不能写入迁移验证。");
  }
  const review = job.review ?? (await api.getGroundedReview(job.reviewVersionId)).data;
  const nextDraft: EssayTransferDraft = {
    ...draft,
    phase: "review_ready",
    reviewVersionId: review.reviewVersionId,
    decisions: draft.decisions.length ? draft.decisions : decisionsForReview(review),
  };
  const options = criterionOptions(sourceReview, review, nextDraft.selectedCriterionCodes);
  if (!options.length) {
    throw new EssayClosureError("源评阅与新题评阅没有共同评分维度，不能猜测迁移标准。");
  }
  return { state: "ready", review, draft: nextDraft, criterionOptions: options };
}

function persist(
  context: Pick<EssayTransferFlowContext, "persist">,
  draft: EssayTransferDraft,
): EssayTransferDraft {
  context.persist(draft);
  return draft;
}

export function transferPhaseMessage(phase: EssayTransferDraft["phase"]): string {
  return {
    drafting: "正在建立新题独立作答记录…",
    attempt_saved: "新题作答记录已建立，正在写入答案版本…",
    answer_saved: "新题答案已保存，正在建立证据化评阅…",
    review_queued: "新题评阅正在排队或执行…",
    review_ready: "正在写入你对新题评阅的逐条决定…",
    decision_saved: "问题决定已保存，正在写入迁移验证与掌握状态…",
    context_invalid: "迁移任务上下文已失效。",
    completed: "迁移验证已完成。",
  }[phase];
}
