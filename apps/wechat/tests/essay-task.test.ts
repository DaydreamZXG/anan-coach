import { afterEach, describe, expect, it, vi } from "vitest";
import {
  EssayIntelligenceApi,
  type EssayReviewIntentView,
  type EssayReviewResponse,
  type EssayTaskWorkspace,
} from "../miniprogram/api/essay-intelligence";
import { ApiClient } from "../miniprogram/api/client";
import { registeredTaskNavigationTarget } from "../miniprogram/app/navigation/routes";
import {
  buildDraftingCompletionCommand,
  buildReviewQueueCommand,
  initialEssayDraftingDraft,
  isPendingEssayDraftingSubmission,
} from "../miniprogram/features/essay-task/drafting";
import {
  buildArtifactCommand,
  EssayFormError,
  initialEssayFormDrafts,
} from "../miniprogram/features/essay-task/forms";
import {
  presentEssayWorkspace,
  stageBlockingReason,
} from "../miniprogram/features/essay-task/presentation";
import {
  presentGroundedReview,
  presentReviewIntent,
  presentReviewJob,
} from "../miniprogram/features/essay-task/review-presentation";
import { createIdempotencyKey } from "../miniprogram/platform/command-id";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("essay task capability registry", () => {
  it("opens artifact stages and the v2 attempt-answer drafting writer", () => {
    const reading = registeredTaskNavigationTarget(
      "/pages/essay-task/index?mode=material-reading&taskVersionId=task-1&stage=essay_material_reading&planTaskId=plan-task-1",
    );

    expect(reading).toMatchObject({ mode: "navigateTo" });
    expect(
      registeredTaskNavigationTarget(
        "/pages/essay-task/index?mode=baseline&taskVersionId=task-1&stage=essay_drafting&planTaskId=plan-task-1",
      ),
    ).toMatchObject({ mode: "navigateTo" });
    expect(
      registeredTaskNavigationTarget("/pages/essay-task/index?mode=practice&taskVersionId=task-1"),
    ).toBeNull();
  });
});

describe("essay drafting workflow", () => {
  it("binds attempt timing/source and saves the answer with a real thinking snapshot", () => {
    const workspace = createWorkspace({ rubric: true });
    const draft = initialEssayDraftingDraft();
    draft.answerText = "基层治理应建立跨部门协同机制，让数据多跑路、群众少跑腿。";
    draft.userThought = "我先按主体和机制找点，再压缩成一段。";
    draft.pointFindingText = "抓住协同平台和群众体验两处材料。";
    draft.outlineText = "问题—机制—效果";
    draft.sourceContext = "paper";
    draft.sourceContextIndex = 1;
    draft.sourceType = "ocr_confirmed";
    draft.sourceTypeIndex = 1;
    draft.elapsedMinutesText = "35";
    draft.reflectionCauseCodes = ["wrong_abstraction", "weak_structure"];
    draft.whatIWillChange = "先归并材料事实，再写上位概念。";
    draft.verificationPlan = "下一题逐点核对概念层级和材料覆盖。";

    expect(
      buildDraftingCompletionCommand(workspace, draft, 12, {
        planTaskId: "plan-task-1",
        basedOnExecutionVersionId: "execution-version-1",
      }),
    ).toEqual({
      planTaskId: "plan-task-1",
      taskVersionId: "task-version-1",
      materialBundleVersionId: "material-version-1",
      rubricVersionId: "rubric-version-1",
      elapsedMinutes: 35,
      basedOnExecutionVersionId: "execution-version-1",
      attempt: {
        mode: "create",
        sourceContext: "paper",
        timedStatus: "timed",
        elapsedSeconds: 2100,
      },
      answer: {
        mode: "create",
        sourceType: "ocr_confirmed",
        answerText: draft.answerText,
        thinking: {
          userThought: draft.userThought,
          pointFindingText: draft.pointFindingText,
          outlineText: draft.outlineText,
        },
      },
      reflection: {
        schemaType: "reflection",
        causeCodes: ["wrong_abstraction", "weak_structure"],
        whatIThought: draft.userThought,
        whatIWillChange: draft.whatIWillChange,
        verificationPlan: draft.verificationPlan,
      },
    });
    expect(buildReviewQueueCommand("attempt-1", "answer-version-1")).toEqual({
      attemptId: "attempt-1",
      answerVersionId: "answer-version-1",
      reviewScope: "grounded_full",
    });
  });

  it("recognizes only the same persisted task command for safe resume", () => {
    const workspace = createWorkspace({ rubric: true });
    const draft = initialEssayDraftingDraft();
    draft.answerText = "完整答案";
    draft.userThought = "真实思路";
    draft.reflectionCauseCodes = ["expression"];
    draft.whatIWillChange = "压缩重复表达";
    draft.verificationPlan = "下一题检查每句话的信息增量";
    const command = buildDraftingCompletionCommand(workspace, draft, 60, {
      planTaskId: "plan-task-1",
    });
    const pending = {
      phase: "completion_pending" as const,
      taskVersionId: "task-version-1",
      planTaskId: "plan-task-1",
      draft,
      completion: {
        idempotencyKey: "essay-drafting-command-1",
        command,
      },
    };

    expect(
      isPendingEssayDraftingSubmission(pending, {
        taskVersionId: "task-version-1",
        planTaskId: "plan-task-1",
      }),
    ).toBe(true);
    expect(
      isPendingEssayDraftingSubmission(pending, {
        taskVersionId: "another-task",
        planTaskId: "plan-task-1",
      }),
    ).toBe(false);
    expect(
      isPendingEssayDraftingSubmission(
        {
          ...pending,
          completion: { ...pending.completion, command: { ...command, planTaskId: "other" } },
        },
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
      ),
    ).toBe(false);
    expect(
      isPendingEssayDraftingSubmission(
        {
          ...pending,
          completion: {
            ...pending.completion,
            command: {
              ...command,
              reflection: { ...command.reflection, verificationPlan: "" },
            },
          },
        },
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
      ),
    ).toBe(false);
  });

  it("confirms an existing owned answer and still requires a structured reflection", () => {
    const workspace = createWorkspace({ rubric: true });
    workspace.progression.attemptId = "attempt-1";
    workspace.progression.answerVersionId = "answer-version-1";
    const draft = initialEssayDraftingDraft(workspace);
    draft.userThought = "交卷时我为了凑齐要点，保留了太多材料原词。";
    draft.reflectionCauseCodes = ["wrong_abstraction"];
    draft.whatIWillChange = "先写上位概念，再核对材料覆盖。";
    draft.verificationPlan = "下一道概括题检查每个要点的概念层级。";

    expect(
      buildDraftingCompletionCommand(workspace, draft, 180, {
        planTaskId: "plan-task-1",
      }),
    ).toMatchObject({
      attempt: { mode: "existing", attemptId: "attempt-1" },
      answer: { mode: "existing", answerVersionId: "answer-version-1" },
      reflection: {
        schemaType: "reflection",
        causeCodes: ["wrong_abstraction"],
        whatIThought: draft.userThought,
        whatIWillChange: draft.whatIWillChange,
        verificationPlan: draft.verificationPlan,
      },
    });
  });

  it("does not create an atomic command without all required reflection fields", () => {
    const workspace = createWorkspace({ rubric: true });
    const draft = initialEssayDraftingDraft();
    draft.answerText = "完整答案";
    draft.userThought = "真实思路";

    expect(() =>
      buildDraftingCompletionCommand(workspace, draft, 60, {
        planTaskId: "plan-task-1",
      }),
    ).toThrow("下一次作答要具体改变什么");
  });

  it("round-trips only the review recovery identifiers without retaining answer bodies", () => {
    const pending = {
      phase: "review_pending" as const,
      taskVersionId: "task-version-1",
      planTaskId: "plan-task-1",
      receipt: {
        attemptId: "attempt-1",
        answerVersionId: "answer-version-1",
        reflectionArtifactVersionId: "reflection-version-1",
      },
      intent: {
        intentId: "intent-1",
        intentVersionId: "intent-version-1",
      },
      ensure: {
        idempotencyKey: "essay-review-ensure-1",
      },
    };
    const restored: unknown = JSON.parse(JSON.stringify(pending));

    expect(
      isPendingEssayDraftingSubmission(restored, {
        taskVersionId: "task-version-1",
        planTaskId: "plan-task-1",
      }),
    ).toBe(true);
    if (!isPendingEssayDraftingSubmission(restored, pending)) {
      throw new Error("fixture must restore a valid pending review command");
    }
    expect(restored.phase).toBe("review_pending");
    if (restored.phase !== "review_pending") throw new Error("fixture phase must be narrowed");
    expect(restored.ensure?.idempotencyKey).toBe("essay-review-ensure-1");
    expect(
      buildReviewQueueCommand(restored.receipt.attemptId, restored.receipt.answerVersionId),
    ).toEqual({
      attemptId: "attempt-1",
      answerVersionId: "answer-version-1",
      reviewScope: "grounded_full",
    });

    expect(
      isPendingEssayDraftingSubmission(pending, {
        taskVersionId: "task-version-1",
        planTaskId: "plan-task-1",
      }),
    ).toBe(true);
  });

  it("shows only a completed grounded range and its source citations", () => {
    const review = completedReview();
    const display = presentGroundedReview(review);

    expect(display.state).toBe("completed");
    expect(display.result).toMatchObject({
      trainingEstimateLabel: "12–15",
      recommendedLabel: "14",
      maximumLabel: "20",
      confidenceLabel: "低置信度",
      confidenceLow: true,
    });
    expect(display.result?.dimensions[0]).toMatchObject({
      answerEvidence: ["协同机制"],
      materialEvidence: ["让数据多跑路"],
    });
  });

  it("never exposes a partial score from blocked or failed review jobs", () => {
    const blocked = { ...completedReview(), status: "blocked" as const };
    const blockedDisplay = presentGroundedReview(blocked);
    expect(blockedDisplay.state).toBe("blocked");
    expect(blockedDisplay.result).toBeNull();

    const failedJob = {
      jobId: "job-1",
      status: "failed" as const,
      attemptId: "attempt-1",
      answerVersionId: "answer-version-1",
      reviewVersionId: "review-version-1",
      reviewStatus: "completed" as const,
      quota: { feature: "essay_review" as const, used: 1, limit: 20, usageDate: "2026-07-13" },
      createdAt: "2026-07-13T00:00:00Z",
      completedAt: "2026-07-13T00:01:00Z",
      review: completedReview(),
    };
    const failedDisplay = presentReviewJob(failedJob);
    expect(failedDisplay.state).toBe("failed");
    expect(failedDisplay.result).toBeNull();
  });

  it("shows a durable quota wait with the server-provided recovery time", () => {
    const display = presentReviewIntent(
      reviewIntent({
        status: "waiting_quota",
        quota: { feature: "essay_review", used: 20, limit: 20, usageDate: "2026-07-13" },
        retryAfter: "2026-07-14T00:00:00+08:00",
      }),
    );

    expect(display).toMatchObject({
      state: "waiting_quota",
      title: "答案已保存，评阅额度待恢复",
      result: null,
    });
    expect(display.message).toContain("20/20");
    expect(display.message).toContain("07月14日 00:00");
  });
});

describe("essay artifact forms", () => {
  it("binds every material annotation to the immutable task and material versions", () => {
    const workspace = createWorkspace();
    const drafts = initialEssayFormDrafts(workspace);
    drafts.overallTheme = "基层治理需要协同和数字能力";
    drafts.materialAnnotations[0] = {
      ...requiredFirst(drafts.materialAnnotations),
      paragraphSummary: "基层通过协同平台解决重复跑腿问题",
      subjectsText: "基层干部、群众",
      evidenceQuote: "让数据多跑路，让群众少跑腿",
    };

    expect(buildArtifactCommand("essay_material_reading", workspace, drafts)).toEqual({
      artifactType: "material_annotation",
      taskVersionId: "task-version-1",
      materialBundleVersionId: "material-version-1",
      payload: {
        schemaType: "material_annotation",
        overallTheme: "基层治理需要协同和数字能力",
        annotations: [
          {
            segmentId: "segment-1",
            paragraphSummary: "基层通过协同平台解决重复跑腿问题",
            subjects: ["基层干部", "群众"],
            evidenceQuote: "让数据多跑路，让群众少跑腿",
          },
        ],
      },
    });
  });

  it("refuses point extraction without its canonical material annotation dependency", () => {
    const workspace = createWorkspace({ rubric: true });
    const drafts = initialEssayFormDrafts(workspace);
    drafts.points[0] = {
      ...requiredFirst(drafts.points),
      statement: "建立跨部门协同机制",
      sourceSegmentIds: ["segment-1"],
    };

    expect(() => buildArtifactCommand("essay_point_extraction", workspace, drafts)).toThrow(
      EssayFormError,
    );
    expect(stageBlockingReason("essay_point_extraction", workspace)).toContain("材料地图");
  });

  it("requires acknowledgement to cover every finding while limiting priorities", () => {
    const workspace = createWorkspace({ rubric: true, review: true });
    const drafts = initialEssayFormDrafts(workspace);
    drafts.review = {
      acknowledgedFindingIds: ["finding-1"],
      priorityFindingIds: ["finding-1"],
      learnerUnderstanding: "我把现象误写成原因，下一版要回到材料关系。",
    };
    expect(() => buildArtifactCommand("essay_review", workspace, drafts)).toThrow("全部问题");

    drafts.review.acknowledgedFindingIds = ["finding-1", "finding-2"];
    expect(buildArtifactCommand("essay_review", workspace, drafts)).toMatchObject({
      artifactType: "review_acknowledgement",
      rubricVersionId: "rubric-version-1",
      reviewVersionId: "review-version-1",
      payload: {
        schemaType: "review_acknowledgement",
        acknowledgedFindingIds: ["finding-1", "finding-2"],
        priorityFindingIds: ["finding-1"],
      },
    });
  });

  it("keeps long material order and exposes stable page anchors", () => {
    const workspace = createWorkspace();
    workspace.material.segments.push({
      materialId: "material-2",
      segmentId: "segment-2",
      materialOrder: 2,
      segmentOrder: 1,
      text: "第二则材料。",
    });

    const view = presentEssayWorkspace(workspace, "material_annotation");
    expect(view.directory).toEqual([
      {
        materialOrder: 1,
        label: "材料 1",
        firstAnchorId: "material-segment-1",
        segmentCount: 1,
      },
      {
        materialOrder: 2,
        label: "材料 2",
        firstAnchorId: "material-segment-2",
        segmentCount: 1,
      },
    ]);
  });
});

describe("EssayIntelligenceApi", () => {
  it("creates essay command IDs from WeChat cryptographic entropy", async () => {
    const entropy = new Uint8Array(16);
    const getRandomValues = vi.fn().mockResolvedValue({ randomValues: entropy.buffer });
    vi.stubGlobal("wx", { getRandomValues });

    await expect(createIdempotencyKey("essay-complete")).resolves.toBe(
      "essay-complete-00000000-0000-4000-8000-000000000000",
    );
    expect(getRandomValues).toHaveBeenCalledWith({ length: 16 });
  });

  it("uses authenticated workspace and idempotent artifact endpoints", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      if (options.method === "GET") {
        expect(options.url).toBe(
          "https://api.example.test/api/v2/essay-intelligence/tasks/task%2Fversion/workspace",
        );
        options.success?.(success(createWorkspace()));
        return;
      }
      expect(options.url).toBe(
        "https://api.example.test/api/v2/essay-intelligence/artifacts/complete-plan-task",
      );
      expect(options.header).toMatchObject({
        Authorization: "Bearer token",
        "Idempotency-Key": "essay-command-1",
      });
      expect(options.data).toMatchObject({
        planTaskId: "plan-task-1",
        elapsedMinutes: 12,
        artifact: { artifactType: "material_annotation" },
      });
      options.success?.(
        success({ artifactVersionId: "artifact-version-1", artifactType: "material_annotation" }),
      );
    });
    vi.stubGlobal("wx", { request });
    const api = new EssayIntelligenceApi(new ApiClient("https://api.example.test", "token"));

    await api.getWorkspace("task/version");
    await api.completePlanTask(
      {
        planTaskId: "plan-task-1",
        elapsedMinutes: 12,
        artifact: {
          artifactType: "material_annotation",
          taskVersionId: "task-version-1",
          materialBundleVersionId: "material-version-1",
          payload: { schemaType: "material_annotation", overallTheme: "主题", annotations: [] },
        },
      },
      "essay-command-1",
    );

    expect(request).toHaveBeenCalledTimes(2);
  });

  it("uses one atomic drafting command before ensuring the current grounded review", async () => {
    const calls: WechatMiniprogram.RequestOption[] = [];
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      calls.push(options);
      if (options.url.endsWith("/drafting/complete-plan-task")) {
        options.success?.(
          success({
            attempt: { attemptId: "attempt-1" },
            answer: { answerVersionId: "answer-version-1" },
            reflection: { artifactVersionId: "reflection-version-1" },
            completion: { plan: {} },
            reviewIntent: reviewIntent({ status: "pending" }),
          }),
        );
      } else if (options.url.endsWith("/reviews/current/ensure")) {
        options.success?.(
          success({
            intent: reviewIntent({ status: "queued", jobId: "job-1", jobStatus: "queued" }),
            job: { jobId: "job-1", status: "queued" },
          }),
        );
      } else {
        options.success?.(success({ jobId: "job-1", status: "running" }));
      }
    });
    vi.stubGlobal("wx", { request });
    const api = new EssayIntelligenceApi(new ApiClient("https://api.example.test", "token"));

    await api.completeDraftingPlanTask(
      {
        planTaskId: "plan-task-1",
        taskVersionId: "task-version-1",
        materialBundleVersionId: "material-version-1",
        rubricVersionId: "rubric-version-1",
        elapsedMinutes: 10,
        attempt: {
          mode: "create",
          sourceContext: "in_program",
          timedStatus: "timed",
          elapsedSeconds: 600,
        },
        answer: {
          mode: "create",
          sourceType: "typed",
          answerText: "完整答案",
          thinking: { userThought: "真实思路" },
        },
        reflection: {
          schemaType: "reflection",
          causeCodes: ["expression"],
          whatIThought: "真实思路",
          whatIWillChange: "压缩重复表达",
          verificationPlan: "下一题检查每句话的信息增量",
        },
      },
      "drafting-command-1",
    );
    await api.ensureCurrentGroundedReview(
      {
        taskVersionId: "task-version-1",
        materialBundleVersionId: "material-version-1",
        rubricVersionId: "rubric-version-1",
        reviewScope: "grounded_full",
      },
      "review-ensure-command-1",
    );
    await api.getGroundedReviewJob("job/1");
    await api.regenerateGroundedReview("job/1", "review-regenerate-job-1");

    expect(calls.map((item) => item.url)).toEqual([
      "https://api.example.test/api/v2/essay-intelligence/drafting/complete-plan-task",
      "https://api.example.test/api/v2/essay-intelligence/reviews/current/ensure",
      "https://api.example.test/api/v2/essay-intelligence/review-jobs/job%2F1",
      "https://api.example.test/api/v2/essay-intelligence/review-jobs/job%2F1/regenerate",
    ]);
    expect(calls.slice(0, 2).map((item) => requestHeader(item, "Idempotency-Key"))).toEqual([
      "drafting-command-1",
      "review-ensure-command-1",
    ]);
    expect(calls.every((item) => requestHeader(item, "Authorization") === "Bearer token")).toBe(
      true,
    );
    expect(requestHeader(requiredFirst(calls.slice(3)), "Idempotency-Key")).toBe(
      "review-regenerate-job-1",
    );
    expect(requiredFirst(calls.slice(3)).data).toEqual({});
  });
});

function createWorkspace(options: { rubric?: boolean; review?: boolean } = {}): EssayTaskWorkspace {
  const rubric = options.rubric
    ? {
        rubricVersionId: "rubric-version-1",
        sourceKind: "system_compiled" as const,
        verificationStatus: "unverified" as const,
        totalMaximum: 20,
        criteria: [
          {
            criterionCode: "content",
            label: "内容",
            maximum: 20,
            scoringRule: "按材料覆盖度给分",
            materialEvidenceRequired: true,
          },
        ],
        points: [
          {
            rubricPointId: "rubric-point-1",
            criterionCode: "content",
            category: "countermeasure",
            label: "跨部门协同",
            maximumCredit: 5,
            materialSegmentIds: ["segment-1"],
            materialEvidenceRefs: [
              {
                materialBundleVersionId: "material-version-1",
                materialId: "material-1",
                segmentId: "segment-1",
                startOffset: 0,
                endOffset: 7,
                quote: "让数据多跑路",
                quoteSha256: "c".repeat(64),
              },
            ],
          },
        ],
      }
    : null;
  const findings = options.review
    ? [finding("finding-1", "没有区分现象和原因"), finding("finding-2", "对策缺少材料支撑")]
    : [];
  return {
    task: {
      taskVersionId: "task-version-1",
      taskKey: "guokao:2025:essay:1",
      prompt: "根据给定材料，概括基层治理的经验。",
      requirements: "全面、准确，不超过 300 字。",
      questionType: "summary",
      wordLimit: 300,
      maximumScore: 20,
    },
    material: {
      materialBundleVersionId: "material-version-1",
      sourceKind: "own_bank",
      segments: [
        {
          materialId: "material-1",
          segmentId: "segment-1",
          materialOrder: 1,
          segmentOrder: 1,
          text: "让数据多跑路，让群众少跑腿。",
        },
      ],
    },
    rubricAvailability: { status: rubric ? "published" : "pending", rubric },
    currentArtifacts: {},
    progression: {
      attemptId: options.review ? "attempt-1" : null,
      answerVersionId: options.review ? "answer-version-1" : null,
      reviewVersionId: options.review ? "review-version-1" : null,
      reviewJobId: null,
      reviewStatus: options.review ? "completed" : null,
      rewriteVersionId: null,
      transferVerificationVersionId: null,
      transferStatus: null,
      currentReview: options.review
        ? { reviewVersionId: "review-version-1", status: "completed", findings }
        : null,
    },
  };
}

function completedReview(): EssayReviewResponse {
  const answerEvidence = {
    answerVersionId: "answer-version-1",
    segmentId: "answer-segment-1",
    quote: "协同机制",
    quoteSha256: "a".repeat(64),
  };
  const materialEvidence = {
    materialBundleVersionId: "material-version-1",
    materialId: "material-1",
    segmentId: "segment-1",
    quote: "让数据多跑路",
    quoteSha256: "b".repeat(64),
  };
  return {
    reviewVersionId: "review-version-1",
    attemptId: "attempt-1",
    taskVersionId: "task-version-1",
    answerVersionId: "answer-version-1",
    materialBundleVersionId: "material-version-1",
    rubricVersionId: "rubric-version-1",
    reviewScope: "grounded_full",
    status: "completed",
    readiness: {
      promptComplete: true,
      materialComplete: true,
      answerComplete: true,
      ocrConfirmed: true,
      blockingCodes: [],
    },
    score: {
      scoreRange: { lower: 12, recommended: 14, upper: 15 },
      maximum: 20,
      confidence: "low",
      confidenceReasons: ["评分规程由系统编制，尚未人工核验"],
    },
    dimensions: [
      {
        criterionCode: "content",
        label: "内容",
        scoreRange: { lower: 12, recommended: 14, upper: 15 },
        maximum: 20,
        rationale: "覆盖主要机制，但效果表达仍可压缩。",
        findingIds: ["finding-1"],
        answerEvidenceRefs: [answerEvidence],
        materialEvidenceRefs: [materialEvidence],
      },
    ],
    pointCoverage: [],
    findings: [finding("finding-1", "效果表达没有完全贴合材料")],
    claimEvidence: [],
    rewritePlan: null,
    audit: {
      contractVersion: "essay-review-v2",
      providerSchemaVersion: "essay-provider-review-v2",
      rubricVersion: "rubric-version-1",
      promptVersion: "grounded-essay-review-v1",
      provider: "gemini",
      model: "gemini-3.5-flash",
      inputFingerprint: "d".repeat(64),
      outputFingerprint: "e".repeat(64),
      rubricSource: "system_compiled",
      rubricVerification: "unverified",
      generatedAt: "2026-07-13T00:00:00Z",
      validUntil: "2026-08-12T00:00:00Z",
    },
    thinkingEvidenceAvailable: true,
  };
}

function reviewIntent(overrides: Partial<EssayReviewIntentView> = {}): EssayReviewIntentView {
  return {
    intentId: "intent-1",
    intentVersionId: "intent-version-1",
    versionNo: 1,
    status: "pending",
    attemptId: "attempt-1",
    answerVersionId: "answer-version-1",
    taskVersionId: "task-version-1",
    materialBundleVersionId: "material-version-1",
    rubricVersionId: "rubric-version-1",
    reviewScope: "grounded_full",
    inputFingerprint: "a".repeat(64),
    intentFingerprint: "b".repeat(64),
    stateFingerprint: "c".repeat(64),
    jobId: null,
    jobStatus: null,
    quota: null,
    retryAfter: null,
    supersedesIntentVersionId: null,
    createdAt: "2026-07-13T00:00:00Z",
    ...overrides,
  };
}

function finding(findingId: string, diagnosis: string) {
  return {
    findingId,
    type: "abstraction" as const,
    severity: "high" as const,
    diagnosis,
    action: "回到材料核对逻辑关系",
    attribution: "written_imprecisely" as const,
    answerEvidenceRefs: [
      {
        answerVersionId: "answer-version-1",
        segmentId: "answer-segment-1",
        quote: "原答案",
        quoteSha256: "a".repeat(64),
      },
    ],
    materialEvidenceRefs: [
      {
        materialBundleVersionId: "material-version-1",
        materialId: "material-1",
        segmentId: "segment-1",
        quote: "材料原句",
        quoteSha256: "b".repeat(64),
      },
    ],
  };
}

function requiredFirst<T>(values: T[]): T {
  const first = values[0];
  if (!first) throw new Error("fixture requires one item");
  return first;
}

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "essay-request-1",
      generatedAt: "2026-07-13T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}

function requestHeader(request: WechatMiniprogram.RequestOption, name: string): string | undefined {
  const headers: unknown = request.header;
  if (!headers || typeof headers !== "object") return undefined;
  const value = (headers as Record<string, unknown>)[name];
  return typeof value === "string" ? value : undefined;
}
