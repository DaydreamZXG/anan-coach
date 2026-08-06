import { afterEach, describe, expect, it, vi } from "vitest";
import type {
  EssayIntelligenceApi,
  EssayReviewResponse,
  EssayTaskWorkspace,
} from "../miniprogram/api/essay-intelligence";
import {
  essayStageCapability,
  parseEssayWriterContext,
} from "../miniprogram/features/essay-task/capabilities";
import {
  buildRewritePlanTaskCompletionCommand,
  buildRewriteCommand,
  buildTransferCommand,
  buildTransferPlanTaskCompletionCommand,
  criterionOptions,
  initialRewriteDraft,
  initialTransferDraft,
} from "../miniprogram/pages/essay-task/features/closure";
import {
  advanceEssayTransferFlow,
  pollEssayTransferReview,
} from "../miniprogram/pages/essay-task/features/closure-flow";
import {
  loadEssayClosureDraft,
  storeEssayClosureDraft,
} from "../miniprogram/pages/essay-task/features/closure-store";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("essay closure capability and commands", () => {
  it("opens rewrite only when every frozen writer parameter including attemptId is present", () => {
    const missingAttempt = parseEssayWriterContext("essay_rewrite", {
      taskVersionId: "task-1",
      baseReviewVersionId: "review-1",
      baseAnswerVersionId: "answer-1",
      findingDecisionArtifactVersionId: "decision-1",
      basedOnAnswerVersionId: "answer-1",
    });
    expect(missingAttempt).toBeNull();
    expect(essayStageCapability("essay_rewrite", missingAttempt).writable).toBe(false);

    const context = parseEssayWriterContext("essay_rewrite", {
      attemptId: "attempt-1",
      baseReviewVersionId: "review-1",
      baseAnswerVersionId: "answer-1",
      findingDecisionArtifactVersionId: "decision-1",
      basedOnAnswerVersionId: "answer-1",
    });
    expect(context).toMatchObject({ kind: "rewrite", attemptId: "attempt-1" });
    expect(essayStageCapability("essay_rewrite", context)).toMatchObject({
      writable: true,
      planningCompletionWritable: true,
    });
  });

  it("writes a CAS rewrite only after every accepted finding has a learner resolution", () => {
    const review = reviewFixture(
      "source-task",
      "source-material",
      "source-answer",
      "source-review",
    );
    const draft = initialRewriteDraft(review, ["finding-1"]);
    draft.answerText = "改写后的完整答案";
    draft.resolutions[0] = {
      ...requiredAt(draft.resolutions, 0),
      status: "resolved",
      statusIndex: 1,
      learnerEvidence: "新答案第一句已改为上位概念并补充材料依据。",
    };

    expect(
      buildRewriteCommand(
        {
          kind: "rewrite",
          attemptId: "attempt-1",
          baseReviewVersionId: "source-review",
          baseAnswerVersionId: "source-answer",
          findingDecisionArtifactVersionId: "decision-1",
          basedOnAnswerVersionId: "source-answer",
        },
        draft,
        ["finding-1"],
      ),
    ).toMatchObject({
      attemptId: "attempt-1",
      basedOnAnswerVersionId: "source-answer",
      answerText: "改写后的完整答案",
      resolutions: [
        {
          findingId: "finding-1",
          status: "resolved",
          learnerEvidence: "新答案第一句已改为上位概念并补充材料依据。",
        },
      ],
    });
  });

  it("allows only an explicitly selected source/transfer criterion intersection", () => {
    const source = reviewFixture(
      "source-task",
      "source-material",
      "source-answer",
      "source-review",
    );
    const target = reviewFixture(
      "target-task",
      "target-material",
      "target-answer",
      "target-review",
    );
    source.dimensions = [dimension("point_coverage", "要点覆盖"), dimension("structure", "结构")];
    target.dimensions = [dimension("point_coverage", "要点覆盖"), dimension("expression", "表达")];
    const options = criterionOptions(source, target, ["point_coverage"]);
    expect(options).toEqual([
      { criterionCode: "point_coverage", label: "要点覆盖", checked: true },
    ]);

    const draft = initialTransferDraft();
    draft.attemptId = "target-attempt";
    draft.answerVersionId = "target-answer";
    draft.reviewVersionId = "target-review";
    draft.findingDecisionArtifactVersionId = "target-decision";
    draft.selectedCriterionCodes = ["expression"];
    expect(() =>
      buildTransferCommand(
        {
          kind: "transfer",
          targetTaskVersionId: "target-task",
          targetMaterialBundleVersionId: "target-material",
          sourceRewriteVersionId: "rewrite-1",
          sourceFindingDecisionArtifactVersionId: "source-decision",
        },
        draft,
        options.map((item) => item.criterionCode),
      ),
    ).toThrow("对照的维度得是两题都评过的");
  });

  it("wraps each closure writer with the frozen plan completion context", () => {
    const rewrite = {
      attemptId: "attempt-1",
      baseReviewVersionId: "review-1",
      baseAnswerVersionId: "answer-1",
      findingDecisionArtifactVersionId: "decision-1",
      basedOnAnswerVersionId: "answer-1",
      sourceType: "typed" as const,
      answerText: "改写答案",
      resolutions: [
        { findingId: "finding-1", status: "resolved" as const, learnerEvidence: "已修正" },
      ],
    };
    expect(
      buildRewritePlanTaskCompletionCommand(rewrite, "plan-rewrite", 18, "execution-version-1"),
    ).toEqual({
      rewrite,
      planTaskId: "plan-rewrite",
      elapsedMinutes: 18,
      basedOnExecutionVersionId: "execution-version-1",
    });
    const transfer = {
      sourceRewriteVersionId: "rewrite-1",
      sourceFindingDecisionArtifactVersionId: "decision-1",
      transferAttemptId: "attempt-2",
      transferAnswerVersionId: "answer-2",
      transferReviewVersionId: "review-2",
      transferFindingDecisionArtifactVersionId: "decision-2",
      testedCriterionCodes: ["point_coverage"],
    };
    expect(buildTransferPlanTaskCompletionCommand(transfer, "plan-transfer", 25)).toEqual({
      transfer,
      planTaskId: "plan-transfer",
      elapsedMinutes: 25,
    });
  });
});

describe("essay closure recovery", () => {
  it("persists the whole atomic rewrite-plus-plan command with one idempotency key", () => {
    const storage = memoryStorage();
    vi.stubGlobal("wx", storage.wx);
    const rewriteDraft = initialRewriteDraft(
      reviewFixture("source-task", "source-material", "source-answer", "source-review"),
      ["finding-1"],
    );
    const writerContext = {
      kind: "rewrite" as const,
      attemptId: "attempt-1",
      baseReviewVersionId: "source-review",
      baseAnswerVersionId: "source-answer",
      findingDecisionArtifactVersionId: "decision-1",
      basedOnAnswerVersionId: "source-answer",
    };
    storeEssayClosureDraft("user-a", {
      kind: "rewrite",
      taskVersionId: "source-task",
      planTaskId: "plan-rewrite",
      writerContext,
      draft: rewriteDraft,
      idempotencyKey: "rewrite-atomic-command",
      atomicCommand: buildRewritePlanTaskCompletionCommand(
        {
          attemptId: "attempt-1",
          baseReviewVersionId: "source-review",
          baseAnswerVersionId: "source-answer",
          findingDecisionArtifactVersionId: "decision-1",
          basedOnAnswerVersionId: "source-answer",
          sourceType: "typed",
          answerText: "改写答案",
          resolutions: [{ findingId: "finding-1", status: "resolved", learnerEvidence: "已修正" }],
        },
        "plan-rewrite",
        18,
      ),
    });

    expect(
      loadEssayClosureDraft("user-a", {
        kind: "rewrite",
        taskVersionId: "source-task",
        planTaskId: "plan-rewrite",
        writerContext,
      }),
    ).toMatchObject({
      idempotencyKey: "rewrite-atomic-command",
      atomicCommand: {
        planTaskId: "plan-rewrite",
        elapsedMinutes: 18,
      },
    });
    expect(
      loadEssayClosureDraft("user-b", {
        kind: "rewrite",
        taskVersionId: "source-task",
        planTaskId: "plan-rewrite",
        writerContext,
      }),
    ).toBeNull();
  });

  it("restores transfer at decision_saved with the whole atomic completion command", () => {
    const storage = memoryStorage();
    vi.stubGlobal("wx", storage.wx);
    const draft = initialTransferDraft();
    const writerContext = {
      kind: "transfer" as const,
      targetTaskVersionId: "target-task",
      targetMaterialBundleVersionId: "target-material",
      sourceRewriteVersionId: "rewrite-1",
      sourceFindingDecisionArtifactVersionId: "source-decision",
    };
    Object.assign(draft, {
      phase: "decision_saved",
      attemptId: "target-attempt",
      answerVersionId: "target-answer",
      reviewJobId: "target-review-job",
      reviewVersionId: "target-review",
      findingDecisionArtifactVersionId: "target-decision",
      atomicIdempotencyKey: "transfer-atomic-command",
      atomicCommand: buildTransferPlanTaskCompletionCommand(
        {
          sourceRewriteVersionId: "rewrite-1",
          sourceFindingDecisionArtifactVersionId: "source-decision",
          transferAttemptId: "target-attempt",
          transferAnswerVersionId: "target-answer",
          transferReviewVersionId: "target-review",
          transferFindingDecisionArtifactVersionId: "target-decision",
          testedCriterionCodes: ["point_coverage"],
        },
        "plan-transfer",
        25,
      ),
    });
    storeEssayClosureDraft("user-a", {
      kind: "transfer",
      taskVersionId: "target-task",
      planTaskId: "plan-transfer",
      writerContext,
      draft,
    });

    expect(
      loadEssayClosureDraft("user-a", {
        kind: "transfer",
        taskVersionId: "target-task",
        planTaskId: "plan-transfer",
        writerContext,
      }),
    ).toMatchObject({
      draft: {
        phase: "decision_saved",
        attemptId: "target-attempt",
        answerVersionId: "target-answer",
        reviewVersionId: "target-review",
        atomicIdempotencyKey: "transfer-atomic-command",
        atomicCommand: {
          planTaskId: "plan-transfer",
          elapsedMinutes: 25,
        },
      },
    });
  });

  it("rejects a same-account recovery draft when the frozen writer context changes", () => {
    const storage = memoryStorage();
    vi.stubGlobal("wx", storage.wx);
    const writerContext = {
      kind: "rewrite" as const,
      attemptId: "attempt-1",
      baseReviewVersionId: "source-review",
      baseAnswerVersionId: "source-answer",
      findingDecisionArtifactVersionId: "decision-1",
      basedOnAnswerVersionId: "source-answer",
    };
    storeEssayClosureDraft("user-a", {
      kind: "rewrite",
      taskVersionId: "source-task",
      planTaskId: "plan-rewrite",
      writerContext,
      draft: initialRewriteDraft(
        reviewFixture("source-task", "source-material", "source-answer", "source-review"),
        ["finding-1"],
      ),
    });

    expect(
      loadEssayClosureDraft("user-a", {
        kind: "rewrite",
        taskVersionId: "source-task",
        planTaskId: "plan-rewrite",
        writerContext: { ...writerContext, baseReviewVersionId: "other-review" },
      }),
    ).toBeNull();
  });
});

describe("essay transfer durable flow", () => {
  it("replays the persisted atomic transfer command and returns its planning completion", async () => {
    const draft = initialTransferDraft();
    const atomicCommand = buildTransferPlanTaskCompletionCommand(
      {
        sourceRewriteVersionId: "rewrite-1",
        sourceFindingDecisionArtifactVersionId: "source-decision",
        transferAttemptId: "target-attempt",
        transferAnswerVersionId: "target-answer",
        transferReviewVersionId: "target-review",
        transferFindingDecisionArtifactVersionId: "target-decision",
        testedCriterionCodes: ["point_coverage"],
      },
      "plan-transfer",
      25,
    );
    Object.assign(draft, {
      phase: "decision_saved",
      attemptId: "target-attempt",
      answerVersionId: "target-answer",
      reviewVersionId: "target-review",
      findingDecisionArtifactVersionId: "target-decision",
      selectedCriterionCodes: ["point_coverage"],
      atomicCommand,
      atomicIdempotencyKey: "transfer-atomic-command",
    });
    const completeTransferPlanTask = vi.fn().mockResolvedValue({
      data: {
        transfer: { status: "verified", verificationVersionId: "verification-1" },
        masteryVersions: [],
        completion: { plan: { planVersionId: "plan-v2" } },
      },
    });
    const createKey = vi.fn();

    const result = await advanceEssayTransferFlow({
      api: { completeTransferPlanTask } as unknown as EssayIntelligenceApi,
      workspace: {} as EssayTaskWorkspace,
      writerContext: {
        kind: "transfer",
        targetTaskVersionId: "target-task",
        targetMaterialBundleVersionId: "target-material",
        sourceRewriteVersionId: "rewrite-1",
        sourceFindingDecisionArtifactVersionId: "source-decision",
      },
      draft,
      planTaskId: "plan-transfer",
      elapsedSeconds: 1_500,
      allowedCriterionCodes: ["point_coverage"],
      createIdempotencyKey: createKey,
      persist: vi.fn(),
      isActive: () => true,
    });

    expect(completeTransferPlanTask).toHaveBeenCalledWith(atomicCommand, "transfer-atomic-command");
    expect(createKey).not.toHaveBeenCalled();
    expect(result).toMatchObject({
      state: "completed",
      result: { completion: { plan: { planVersionId: "plan-v2" } } },
    });
  });

  it("turns a completed persisted review job into a review_ready decision draft", async () => {
    const source = reviewFixture(
      "source-task",
      "source-material",
      "source-answer",
      "source-review",
    );
    const target = reviewFixture(
      "target-task",
      "target-material",
      "target-answer",
      "target-review",
    );
    source.dimensions = [dimension("point_coverage", "要点覆盖")];
    target.dimensions = [dimension("point_coverage", "要点覆盖")];
    const draft = initialTransferDraft();
    Object.assign(draft, { phase: "review_queued", reviewJobId: "job-1" });
    const api = {
      getGroundedReviewJob: vi.fn().mockResolvedValue({
        data: {
          jobId: "job-1",
          status: "succeeded",
          reviewStatus: "completed",
          reviewVersionId: "target-review",
          review: target,
        },
      }),
    } as unknown as EssayIntelligenceApi;

    const result = await pollEssayTransferReview(api, draft, source);

    expect(result).toMatchObject({
      state: "ready",
      draft: {
        phase: "review_ready",
        reviewVersionId: "target-review",
        decisions: [{ findingId: "finding-1", decision: "" }],
      },
      criterionOptions: [{ criterionCode: "point_coverage" }],
    });
  });
});

function reviewFixture(
  taskVersionId: string,
  materialBundleVersionId: string,
  answerVersionId: string,
  reviewVersionId: string,
): EssayReviewResponse {
  return {
    attemptId: "attempt-1",
    answerVersionId,
    taskVersionId,
    materialBundleVersionId,
    rubricVersionId: "rubric-1",
    reviewVersionId,
    reviewScope: "grounded_full",
    thinkingEvidenceAvailable: false,
    status: "completed",
    score: null,
    readiness: {
      promptComplete: true,
      materialComplete: true,
      answerComplete: true,
      ocrConfirmed: true,
      blockingCodes: [],
    },
    dimensions: [],
    pointCoverage: [],
    claimEvidence: [],
    findings: [
      {
        findingId: "finding-1",
        type: "abstraction",
        severity: "high",
        diagnosis: "概括层级偏低",
        action: "使用上位概念",
        attribution: "written_imprecisely",
        answerEvidenceRefs: [],
        materialEvidenceRefs: [],
      },
    ],
    rewritePlan: null,
    audit: null,
    supersedesReviewVersionId: null,
  };
}

function dimension(criterionCode: string, label: string) {
  return {
    criterionCode,
    label,
    maximum: 10,
    scoreRange: { lower: 5, upper: 7, recommended: 6 },
    rationale: "根据答案与材料证据核对。",
    findingIds: [],
    answerEvidenceRefs: [],
    materialEvidenceRefs: [],
  };
}

function memoryStorage() {
  const values = new Map<string, unknown>();
  return {
    wx: {
      getStorageSync: (key: string) => values.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => values.set(key, value),
      removeStorageSync: (key: string) => values.delete(key),
    },
  };
}

function requiredAt<T>(items: T[], index: number): T {
  const value = items[index];
  if (value === undefined) throw new Error(`missing fixture at ${String(index)}`);
  return value;
}
