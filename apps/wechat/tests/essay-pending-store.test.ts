import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  promoteToReviewRecovery,
  type PendingEssayDraftingCompletion,
  type PendingEssayReviewRecovery,
} from "../miniprogram/features/essay-task/drafting";
import {
  clearAllEssayPending,
  clearEssayPendingForUser,
  ESSAY_PENDING_TTL_MS,
  loadPendingDraftingSubmission,
  storePendingDraftingSubmission,
} from "../miniprogram/features/essay-task/pending-store";

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", storageWx(storage));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("account-scoped essay pending storage", () => {
  it("never restores account A's command into account B on a shared device", () => {
    const pending = reviewRecovery();
    storePendingDraftingSubmission("user-a", pending, 1_000);

    expect(
      loadPendingDraftingSubmission(
        "user-b",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        2_000,
      ),
    ).toBeNull();
    expect(
      loadPendingDraftingSubmission(
        "user-a",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        2_000,
      ),
    ).toEqual(pending);
  });

  it("expires and deletes a pending command at the seven-day TTL boundary", () => {
    const pending = reviewRecovery();
    storePendingDraftingSubmission("user-a", pending, 5_000);

    expect(
      loadPendingDraftingSubmission(
        "user-a",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        5_000 + ESSAY_PENDING_TTL_MS - 1,
      ),
    ).toEqual(pending);
    expect(
      loadPendingDraftingSubmission(
        "user-a",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        5_000 + ESSAY_PENDING_TTL_MS,
      ),
    ).toBeNull();
    expect(storage.size).toBe(0);
  });

  it("replaces answer and reflection bodies with minimal recovery identifiers after receipt", () => {
    const pending = draftingCompletion();
    storePendingDraftingSubmission("user-a", pending, 10_000);
    expect(JSON.stringify([...storage.values()])).toContain("SENSITIVE-ANSWER");

    const recovery = promoteToReviewRecovery(
      pending,
      {
        attemptId: "attempt-1",
        answerVersionId: "answer-version-1",
        reflectionArtifactVersionId: "reflection-version-1",
      },
      {
        intentId: "intent-1",
        intentVersionId: "intent-version-1",
      },
    );
    storePendingDraftingSubmission("user-a", recovery, 11_000);

    const persisted = JSON.stringify([...storage.values()]);
    expect(persisted).not.toContain("SENSITIVE-ANSWER");
    expect(persisted).not.toContain("SENSITIVE-THOUGHT");
    expect(persisted).not.toContain("SENSITIVE-REFLECTION");
    expect(persisted).not.toContain('"draft"');
    expect(persisted).not.toContain('"completion"');
    expect(persisted).toContain("answer-version-1");
  });

  it("logout cleanup removes the current account and legacy unscoped drafts only", () => {
    const pending = reviewRecovery();
    storePendingDraftingSubmission("user-a", pending, 1_000);
    storePendingDraftingSubmission("user-b", pending, 1_000);
    storage.set("gongkao.essay.pendingDrafting.plan-legacy", { answerText: "legacy" });
    storage.set("unrelated.preference", true);

    clearEssayPendingForUser("user-a");

    expect(
      loadPendingDraftingSubmission(
        "user-a",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        2_000,
      ),
    ).toBeNull();
    expect(
      loadPendingDraftingSubmission(
        "user-b",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        2_000,
      ),
    ).toEqual(pending);
    expect(storage.has("gongkao.essay.pendingDrafting.plan-legacy")).toBe(false);
    expect(storage.get("unrelated.preference")).toBe(true);
  });

  it("clears every account's pending data when no trustworthy identity remains", () => {
    const pending = reviewRecovery();
    storePendingDraftingSubmission("user-a", pending, 1_000);
    storePendingDraftingSubmission("user-b", pending, 1_000);

    clearAllEssayPending();

    expect(storage.size).toBe(0);
  });
});

function reviewRecovery(): PendingEssayReviewRecovery {
  return {
    phase: "review_pending",
    taskVersionId: "task-version-1",
    planTaskId: "plan-task-1",
    receipt: {
      attemptId: "attempt-1",
      answerVersionId: "answer-version-1",
      reflectionArtifactVersionId: "reflection-version-1",
    },
    intent: { intentId: "intent-1", intentVersionId: "intent-version-1" },
    ensure: { idempotencyKey: "essay-review-ensure-1" },
  };
}

function draftingCompletion(): PendingEssayDraftingCompletion {
  const draft: PendingEssayDraftingCompletion["draft"] = {
    answerText: "SENSITIVE-ANSWER",
    userThought: "SENSITIVE-THOUGHT",
    pointFindingText: "材料要点",
    outlineText: "作答结构",
    sourceContext: "in_program",
    sourceContextIndex: 0,
    timedStatus: "timed",
    timedStatusIndex: 0,
    elapsedMinutesText: "30",
    sourceType: "typed",
    sourceTypeIndex: 0,
    sourceArtifactVersionId: "",
    recognitionRevisionId: "",
    reflectionCauseCodes: ["expression"],
    whatIWillChange: "SENSITIVE-REFLECTION",
    verificationPlan: "下一题检查表达",
  };
  return {
    phase: "completion_pending",
    taskVersionId: "task-version-1",
    planTaskId: "plan-task-1",
    draft,
    completion: {
      idempotencyKey: "essay-drafting-command-1",
      command: {
        planTaskId: "plan-task-1",
        taskVersionId: "task-version-1",
        materialBundleVersionId: "material-version-1",
        rubricVersionId: "rubric-version-1",
        elapsedMinutes: 30,
        attempt: {
          mode: "create",
          sourceContext: "in_program",
          timedStatus: "timed",
          elapsedSeconds: 1_800,
        },
        answer: {
          mode: "create",
          sourceType: "typed",
          answerText: draft.answerText,
          thinking: { userThought: draft.userThought },
        },
        reflection: {
          schemaType: "reflection",
          voiceArtifactVersionIds: [],
          causeCodes: ["expression"],
          whatIThought: draft.userThought,
          whatIWillChange: draft.whatIWillChange,
          verificationPlan: draft.verificationPlan,
        },
      },
    },
  };
}

function storageWx(values: Map<string, unknown>) {
  return {
    getStorageSync: (key: string) => values.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => {
      values.set(key, clone(value));
    },
    removeStorageSync: (key: string) => {
      values.delete(key);
    },
    getStorageInfoSync: () => ({
      keys: [...values.keys()],
      currentSize: 0,
      limitSize: 10_240,
    }),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
