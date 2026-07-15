import { afterEach, describe, expect, it, vi } from "vitest";
import { currentAuthSession, storeAuthSession } from "../miniprogram/auth/session";
import type { PendingEssayReviewRecovery } from "../miniprogram/features/essay-task/drafting";
import {
  loadPendingDraftingSubmission,
  storePendingDraftingSubmission,
} from "../miniprogram/features/essay-task/pending-store";

interface ProfilePageDefinition {
  finishLogout(): void;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("profile logout", () => {
  it("removes every signed-in user recovery namespace before clearing identity", async () => {
    const storage = new Map<string, unknown>();
    const globalData: { accessToken?: string; userId?: string } = {};
    const reLaunch = vi.fn();
    let captured: unknown;
    vi.stubGlobal("getApp", () => ({ globalData }));
    vi.stubGlobal("Page", (definition: unknown) => {
      captured = definition;
    });
    vi.stubGlobal("wx", {
      getStorageSync: (key: string) => storage.get(key) ?? "",
      setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
      removeStorageSync: (key: string) => storage.delete(key),
      getStorageInfoSync: () => ({
        keys: [...storage.keys()],
        currentSize: 0,
        limitSize: 10_240,
      }),
      reLaunch,
    });
    const pending = reviewRecovery();
    storeAuthSession("token-a", "user-a");
    storePendingDraftingSubmission("user-a", pending, 1_000);
    storage.set("gongkao.onboarding.pending.v1.user-a.study-preference", { pending: true });
    storage.set("gongkao.positions.v2.user-a.search", {
      query: "",
      examYear: "",
      region: "",
      city: "",
    });
    storage.set("gongkao.positions.v2.user-b.search", {
      query: "",
      examYear: "",
      region: "",
      city: "",
    });
    storage.set("gongkao.navigation.auth-return.v1.user-a", {
      schemaVersion: 1,
      ownerUserId: "user-a",
      createdAtMs: 1_000,
      expiresAtMs: 10_000,
      url: "/pages/positions/index",
    });

    await import("../miniprogram/pages/profile/index");
    const definition = captured as ProfilePageDefinition;
    definition.finishLogout.call(definition);

    expect(currentAuthSession()).toBeUndefined();
    expect(
      loadPendingDraftingSubmission(
        "user-a",
        { taskVersionId: pending.taskVersionId, planTaskId: pending.planTaskId },
        2_000,
      ),
    ).toBeNull();
    expect(storage.has("gongkao.onboarding.pending.v1.user-a.study-preference")).toBe(false);
    expect(storage.has("gongkao.positions.v2.user-a.search")).toBe(false);
    expect(storage.has("gongkao.navigation.auth-return.v1.user-a")).toBe(false);
    expect(storage.has("gongkao.positions.v2.user-b.search")).toBe(true);
    expect(reLaunch).toHaveBeenCalledWith({ url: "/pages/login/index" });
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
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
