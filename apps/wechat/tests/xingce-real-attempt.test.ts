import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "../miniprogram/api/client";
import { XingceLearningApi } from "../miniprogram/api/xingce-learning";
import {
  checkpointCommand,
  runtimeFromXingceSession,
} from "../miniprogram/features/xingce/real_attempt/controller";
import {
  assertLearnerSafeSession,
  resolveXingcePlanTaskId,
} from "../miniprogram/features/xingce/real_attempt/contracts";
import { XingcePageRuntimeRegistry } from "../miniprogram/features/xingce/real_attempt/page-runtime";
import { presentXingceResult } from "../miniprogram/features/xingce/real_attempt/presentation";
import {
  clearXingcePendingForUser,
  loadPendingXingceReflection,
  storePendingXingceReflection,
} from "../miniprogram/features/xingce/real_attempt/pending-store";
import { reduceTaskRuntime } from "../miniprogram/task-runtime/state/runtime";
import {
  activeXingceSession,
  answer,
  apiSuccess,
  completedXingceSession,
  createMemoryWechatStorage,
  submittedXingceSession,
  todayWithXingceTask,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("real xingce attempt contracts", () => {
  it("binds Today resolution to mode, stage, start URL, status and optional exact task ID", () => {
    const dashboard = todayWithXingceTask("targeted");
    expect(resolveXingcePlanTaskId(dashboard, "targeted")).toBe(xingceIds.planTask);
    expect(resolveXingcePlanTaskId(dashboard, "targeted", xingceIds.planTask)).toBe(
      xingceIds.planTask,
    );
    expect(() => resolveXingcePlanTaskId(dashboard, "baseline")).toThrow();
    expect(resolveXingcePlanTaskId(todayWithXingceTask("verification"), "verification")).toBe(
      xingceIds.planTask,
    );
    expect(() => resolveXingcePlanTaskId(dashboard, "targeted", "another-task")).toThrow(
      "不在当前 Today",
    );
  });

  it("rejects duplicate questions, unknown checkpoints, broken materials and leaked scoring data", () => {
    const duplicate = activeXingceSession();
    const firstQuestion = duplicate.bundle.questions[0];
    if (!firstQuestion) throw new Error("fixture question is required");
    duplicate.bundle.questions[1] = { ...firstQuestion };
    expect(() => assertLearnerSafeSession(duplicate)).toThrow("重复题目");

    const unknownAnswer = activeXingceSession();
    unknownAnswer.answers = [
      {
        questionId: "outside-bundle",
        selectedOptionIds: ["A"],
        eliminatedOptionIds: [],
        confidence: "high",
        marked: false,
        skipped: false,
        elapsedSeconds: 1,
        draftText: "",
      },
    ];
    expect(() => assertLearnerSafeSession(unknownAnswer)).toThrow("不匹配");

    const brokenMaterial = activeXingceSession({ sharedMaterial: true });
    brokenMaterial.bundle.materials = [];
    expect(() => assertLearnerSafeSession(brokenMaterial)).toThrow("材料不存在");

    const leaked = activeXingceSession();
    Object.assign(leaked.bundle.questions[0] ?? {}, { answer: "A" });
    expect(() => assertLearnerSafeSession(leaked)).toThrow("答案或解析");
  });

  it("builds a revision-aware checkpoint with tool evidence but excludes untouched answers", () => {
    let runtime = runtimeFromXingceSession(activeXingceSession({ revision: 9 }));
    runtime = reduceTaskRuntime(runtime, { type: "ANSWER_SELECTED", optionIds: ["C"] });
    runtime = reduceTaskRuntime(runtime, {
      type: "OPTION_ELIMINATED",
      optionId: "A",
      eliminated: true,
    });
    runtime = reduceTaskRuntime(runtime, { type: "CONFIDENCE_SET", confidence: "medium" });
    runtime = reduceTaskRuntime(runtime, { type: "QUESTION_MARKED", marked: true });
    runtime = reduceTaskRuntime(runtime, {
      type: "MATERIAL_POSITION_CHANGED",
      position: { anchorId: "material-1", scrollTop: 320, zoom: 1.25 },
    });
    const command = checkpointCommand(runtime);

    expect(command.expectedRevision).toBe(9);
    expect(command.answers).toHaveLength(1);
    expect(command.answers[0]).toMatchObject({
      questionId: "q-1",
      selectedOptionIds: ["C"],
      eliminatedOptionIds: ["A"],
      confidence: "medium",
      marked: true,
      materialPosition: { anchorId: "material-1", scrollTop: 320, zoom: 1.25 },
    });
  });

  it("checkpoints material, elimination and mark evidence before an option is selected", () => {
    let runtime = runtimeFromXingceSession(activeXingceSession({ revision: 4 }));
    runtime = reduceTaskRuntime(runtime, {
      type: "OPTION_ELIMINATED",
      optionId: "B",
      eliminated: true,
    });
    runtime = reduceTaskRuntime(runtime, { type: "QUESTION_MARKED", marked: true });
    runtime = reduceTaskRuntime(runtime, {
      type: "MATERIAL_POSITION_CHANGED",
      position: { anchorId: "material-table", scrollTop: 640, zoom: 1.5 },
    });

    expect(checkpointCommand(runtime).answers).toEqual([
      expect.objectContaining({
        questionId: "q-1",
        selectedOptionIds: [],
        eliminatedOptionIds: ["B"],
        marked: true,
        materialPosition: { anchorId: "material-table", scrollTop: 640, zoom: 1.5 },
      }),
    ]);
  });

  it("keeps timing separate from the verdict and exposes old scoring sessions as read-only", () => {
    const timed = submittedXingceSession("timeout");
    expect(() => assertLearnerSafeSession(timed)).not.toThrow();
    expect(timed.result?.items[0]).toMatchObject({
      objectiveVerdict: "incorrect",
      timingEvidence: { status: "advisory_exceeded" },
    });

    const legacy = submittedXingceSession();
    if (!legacy.result) throw new Error("fixture result is required");
    legacy.result.contractVersion = null;
    legacy.result.groupSummary = null;
    legacy.result.items = legacy.result.items.map((item) => ({
      ...item,
      factContractVersion: null,
      objectiveVerdict: null,
      disposition: null,
      confidenceEvidence: null,
      timingEvidence: null,
    }));
    expect(() => assertLearnerSafeSession(legacy)).not.toThrow();
    expect(presentXingceResult(legacy)).toMatchObject({
      availability: "legacy_result_unavailable",
      items: [],
      group: null,
      verification: null,
    });

    const omittedLegacy = submittedXingceSession();
    if (!omittedLegacy.result) throw new Error("fixture result is required");
    delete omittedLegacy.result.contractVersion;
    delete omittedLegacy.result.groupSummary;
    for (const item of omittedLegacy.result.items) {
      delete item.factContractVersion;
      delete item.objectiveVerdict;
      delete item.disposition;
      delete item.confidenceEvidence;
      delete item.timingEvidence;
    }
    expect(() => assertLearnerSafeSession(omittedLegacy)).not.toThrow();

    const mixed = submittedXingceSession();
    if (mixed.result) mixed.result.contractVersion = null;
    expect(() => assertLearnerSafeSession(mixed)).toThrow("历史行测结果");
  });
});

describe("real xingce pending ownership", () => {
  it("persists the exact reflection command for one account and clears only that owner", () => {
    const { wxStorage } = createMemoryWechatStorage();
    vi.stubGlobal("wx", wxStorage);
    const pending = {
      kind: "reflection" as const,
      sessionId: xingceIds.session,
      reviewId: xingceIds.review,
      idempotencyKey: "reflection-command-stable",
      command: {
        expectedRevision: 1,
        rawText: "我忽略了题干的否定词。",
        tags: ["misread"],
      },
    };
    storePendingXingceReflection("user-a", pending);

    expect(loadPendingXingceReflection("user-a", xingceIds.review)).toEqual(pending);
    expect(loadPendingXingceReflection("user-b", xingceIds.review)).toBeNull();
    clearXingcePendingForUser("user-b");
    expect(loadPendingXingceReflection("user-a", xingceIds.review)).toEqual(pending);
    clearXingcePendingForUser("user-a");
    expect(loadPendingXingceReflection("user-a", xingceIds.review)).toBeNull();
  });

  it("isolates mutable page runtimes and invalidates late callbacks after unload", () => {
    const registry = new XingcePageRuntimeRegistry<object>();
    const pageA = {};
    const pageB = {};
    const runtimeA = registry.attach(pageA);
    const runtimeB = registry.attach(pageB);
    runtimeA.ownerUserId = "user-a";
    runtimeB.ownerUserId = "user-b";
    runtimeA.visible = true;
    const epochA = registry.beginRequest(runtimeA);

    registry.detach(pageA);

    expect(registry.isCurrent(pageA, runtimeA, epochA)).toBe(false);
    expect(runtimeA.disposed).toBe(true);
    expect(runtimeA.visible).toBe(false);
    expect(registry.owns(pageB, runtimeB)).toBe(true);
    expect(runtimeB.ownerUserId).toBe("user-b");
  });
});

describe("XingceLearningApi", () => {
  it("sends CAS, candidate bindings and idempotency headers for every writer", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    const responses = [
      activeXingceSession({ revision: 2 }),
      submittedXingceSession(),
      { ...submittedXingceSession(), revision: 5 },
      { ...submittedXingceSession(), revision: 6 },
      completedXingceSession(),
    ];
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      requests.push(options);
      const response = responses.shift();
      if (!response) throw new Error("unexpected request");
      options.success?.(apiSuccess(response));
    });
    vi.stubGlobal("wx", { request });
    const api = new XingceLearningApi(new ApiClient("https://api.example.test", "token"));

    await api.saveCheckpoint(
      xingceIds.session,
      {
        expectedRevision: 1,
        currentQuestionId: "q-1",
        answers: [answer("q-1", ["A"])],
        totalElapsedSeconds: 1,
      },
      "save-key",
      { leaseId: "00000000-0000-4000-8000-000000000001", generation: 1 },
    );
    await api.submitAttempt(
      xingceIds.session,
      { expectedRevision: 2, totalElapsedSeconds: 2 },
      "submit-key",
      { leaseId: "00000000-0000-4000-8000-000000000001", generation: 1 },
    );
    await api.submitReflection(
      xingceIds.review,
      { expectedRevision: 1, rawText: "原话", tags: ["misread"] },
      "reflection-key",
    );
    await api.decideCause(
      xingceIds.review,
      {
        expectedRevision: 3,
        candidateVersionId: xingceIds.candidate,
        decision: "reject",
      },
      "decision-key",
    );

    await api.submitCauseFollowup(
      xingceIds.review,
      {
        expectedRevision: 2,
        candidateVersionId: xingceIds.candidate,
        answer: "当时没看到否定词。",
      },
      "followup-key",
    );

    expect(requests.map((item) => item.method)).toEqual(["PUT", "POST", "PUT", "PUT", "PUT"]);
    expect(requests.map((item) => idempotencyHeader(item.header))).toEqual([
      "save-key",
      "submit-key",
      "reflection-key",
      "decision-key",
      "followup-key",
    ]);
    expect(requests[2]?.url).toContain(`/attempt-reviews/${xingceIds.review}/reflection`);
    expect(requests[3]?.url).toContain(`/attempt-reviews/${xingceIds.review}/cause-decision`);
    expect(requests[4]?.url).toContain(`/attempt-reviews/${xingceIds.review}/cause-followup`);
  });
});

function idempotencyHeader(value: unknown): string {
  if (typeof value !== "object" || value === null) return "";
  const header = (value as Record<string, unknown>)["Idempotency-Key"];
  return typeof header === "string" ? header : "";
}
