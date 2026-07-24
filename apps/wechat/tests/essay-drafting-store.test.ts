import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { EssayDraftingDraft } from "../miniprogram/features/essay-task/drafting";
import {
  clearEssayDraftingDraftsForUser,
  loadEssayDraftingDraft,
  storeEssayDraftingDraft,
} from "../miniprogram/features/essay-task/drafting-store";

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", storageWx(storage));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("account-scoped essay drafting autosave", () => {
  it("restores only the same account and plan task", () => {
    const draft = draftingDraft();
    storeEssayDraftingDraft(
      "user-a",
      { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
      draft,
      1_000,
    );

    expect(
      loadEssayDraftingDraft(
        "user-a",
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
        2_000,
      ),
    ).toEqual(draft);
    expect(
      loadEssayDraftingDraft(
        "user-b",
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
        2_000,
      ),
    ).toBeNull();
    expect(
      loadEssayDraftingDraft(
        "user-a",
        { taskVersionId: "task-version-1", planTaskId: "plan-task-2" },
        2_000,
      ),
    ).toBeNull();
  });

  it("deletes a stored body when its task version no longer matches", () => {
    storeEssayDraftingDraft(
      "user-a",
      { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
      draftingDraft(),
      1_000,
    );

    expect(
      loadEssayDraftingDraft(
        "user-a",
        { taskVersionId: "task-version-2", planTaskId: "plan-task-1" },
        2_000,
      ),
    ).toBeNull();
    expect(storage.size).toBe(0);
  });

  it("returns a copy instead of exposing the persisted reflection array", () => {
    const draft = draftingDraft();
    storeEssayDraftingDraft(
      "user-a",
      { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
      draft,
      1_000,
    );
    const restored = loadEssayDraftingDraft(
      "user-a",
      { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
      2_000,
    );
    expect(restored).not.toBeNull();
    restored?.reflectionCauseCodes.push("missed_material");

    expect(
      loadEssayDraftingDraft(
        "user-a",
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
        2_000,
      )?.reflectionCauseCodes,
    ).toEqual(["expression"]);
  });

  it("logout cleanup removes only the current account drafts", () => {
    for (const userId of ["user-a", "user-b"]) {
      storeEssayDraftingDraft(
        userId,
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
        draftingDraft(),
        1_000,
      );
    }

    clearEssayDraftingDraftsForUser("user-a");

    expect(
      loadEssayDraftingDraft(
        "user-a",
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
        2_000,
      ),
    ).toBeNull();
    expect(
      loadEssayDraftingDraft(
        "user-b",
        { taskVersionId: "task-version-1", planTaskId: "plan-task-1" },
        2_000,
      ),
    ).toEqual(draftingDraft());
  });
});

function draftingDraft(): EssayDraftingDraft {
  return {
    answerText: "正文",
    userThought: "原始想法",
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
    whatIWillChange: "压缩重复表达",
    verificationPlan: "下一题复查",
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
