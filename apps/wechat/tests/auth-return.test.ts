import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AUTH_RETURN_TTL_MS,
  authenticatedReturnTarget,
  clearAllPendingAuthReturns,
  consumePendingAuthReturn,
  loadPendingAuthReturn,
  loginReturnTarget,
  loginUrlForReturn,
  storePendingAuthReturn,
} from "../miniprogram/app/navigation/auth-return";

const storage = new Map<string, unknown>();

beforeEach(() => {
  storage.clear();
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({ keys: [...storage.keys()], currentSize: 0, limitSize: 10_240 }),
  });
});

describe("authenticated navigation return contract", () => {
  it("builds and parses a canonical login return for a position detail deep link", () => {
    const detail = "/pages/positions/detail/index?positionId=position%2F1";
    const loginUrl = loginUrlForReturn(detail);

    expect(loginUrl).toBe(
      "/pages/login/index?returnTo=%2Fpages%2Fpositions%2Fdetail%2Findex%3FpositionId%3Dposition%252F1",
    );
    expect(loginReturnTarget(loginUrl.split("returnTo=")[1])).toEqual({
      mode: "navigateTo",
      url: detail,
    });
  });

  it("canonicalizes supported position filters and rejects unowned query fields", () => {
    expect(
      authenticatedReturnTarget(
        "/pages/positions/search/index?region=%E6%B1%9F%E8%A5%BF&q=%E7%A8%8E%E5%8A%A1",
      ),
    ).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/search/index?q=%E7%A8%8E%E5%8A%A1&region=%E6%B1%9F%E8%A5%BF",
    });
    expect(
      authenticatedReturnTarget("/pages/positions/detail/index?positionId=p-1&redirect=https://x"),
    ).toBeNull();
    expect(authenticatedReturnTarget("https://example.com")).toBeNull();
    expect(authenticatedReturnTarget("/pages/login/index")).toBeNull();
  });

  it("preserves briefing and score-sheet workspace deep links across login", () => {
    const briefing = "/pages/positions/briefing/index?positionId=position%2F1";
    expect(authenticatedReturnTarget(briefing)).toEqual({ mode: "navigateTo", url: briefing });
    expect(loginUrlForReturn(briefing)).toBe(
      `/pages/login/index?returnTo=${encodeURIComponent(briefing)}`,
    );
    expect(authenticatedReturnTarget("/pages/positions/index?score=1")).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/index?score=1",
    });
    expect(loginReturnTarget(encodeURIComponent("/pages/positions/index?score=1"))).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/index?score=1",
    });
    expect(authenticatedReturnTarget("/pages/positions/index")).toEqual({
      mode: "navigateTo",
      url: "/pages/positions/index",
    });
  });

  it("preserves validated essay and mock task deep links across login", () => {
    const essay =
      "/pages/essay-task/index?taskVersionId=essay-v1&planTaskId=plan-1&stage=essay_material_reading";
    const rewrite =
      "/pages/essay-task/index?stage=essay_rewrite&taskVersionId=essay-v1&planTaskId=plan-1&attemptId=attempt-1&baseReviewVersionId=review-1&baseAnswerVersionId=answer-1&findingDecisionArtifactVersionId=decision-1&basedOnAnswerVersionId=answer-1";
    const mockBySession = "/pages/mock-exam/index?sessionId=mock-session-1";
    const mockByPlan = "/pages/mock-exam/index?planTaskId=plan-1";

    expect(authenticatedReturnTarget(essay)).toEqual({ mode: "navigateTo", url: essay });
    expect(authenticatedReturnTarget(rewrite)).toEqual({ mode: "navigateTo", url: rewrite });
    expect(authenticatedReturnTarget(mockBySession)).toEqual({
      mode: "navigateTo",
      url: mockBySession,
    });
    expect(authenticatedReturnTarget(mockByPlan)).toEqual({
      mode: "navigateTo",
      url: mockByPlan,
    });
    expect(loginUrlForReturn(mockBySession)).toBe(
      `/pages/login/index?returnTo=${encodeURIComponent(mockBySession)}`,
    );
  });

  it("rejects ambiguous or unowned mock task return arguments", () => {
    expect(
      authenticatedReturnTarget(
        "/pages/mock-exam/index?sessionId=mock-session-1&planTaskId=plan-1",
      ),
    ).toBeNull();
    expect(authenticatedReturnTarget("/pages/mock-exam/index?sessionId=%20")).toBeNull();
    expect(authenticatedReturnTarget("/pages/mock-exam/index?redirect=https%3A%2F%2Fx")).toBeNull();
    expect(
      authenticatedReturnTarget(
        "/pages/essay-task/index?stage=essay_drafting&taskVersionId=essay-v1&planTaskId=plan-1&redirect=https%3A%2F%2Fx",
      ),
    ).toBeNull();
    expect(
      authenticatedReturnTarget(
        "/pages/essay-task/index?stage=essay_rewrite&taskVersionId=essay-v1&planTaskId=plan-1",
      ),
    ).toBeNull();
  });

  it("rejects malformed briefing and workspace score queries", () => {
    expect(authenticatedReturnTarget("/pages/positions/briefing/index")).toBeNull();
    expect(authenticatedReturnTarget("/pages/positions/briefing/index?positionId=%20")).toBeNull();
    expect(
      authenticatedReturnTarget("/pages/positions/briefing/index?positionId=p-1&redirect=x"),
    ).toBeNull();
    expect(authenticatedReturnTarget("/pages/positions/index?score=0")).toBeNull();
    expect(authenticatedReturnTarget("/pages/positions/index?score=1&score=1")).toBeNull();
    expect(authenticatedReturnTarget("/pages/positions/index?open=1")).toBeNull();
  });

  it("rejects duplicate, malformed, and incomplete deep-link arguments", () => {
    expect(
      authenticatedReturnTarget("/pages/positions/detail/index?positionId=p-1&positionId=p-2"),
    ).toBeNull();
    expect(authenticatedReturnTarget("/pages/positions/compare/index?ids=p-1")).toBeNull();
    expect(authenticatedReturnTarget("/pages/positions/compare/index?ids=p-1%2Cp-1")).toBeNull();
    expect(loginReturnTarget("%E0%A4%A")).toBeNull();
  });

  it("keeps return intent account-bound, expiring, and one-time", () => {
    storePendingAuthReturn("user-a", "/pages/positions/detail/index?positionId=p-1", 1_000);

    expect(loadPendingAuthReturn("user-b", 2_000)).toBeNull();
    expect(loadPendingAuthReturn("user-a", 2_000)?.url).toBe(
      "/pages/positions/detail/index?positionId=p-1",
    );
    expect(consumePendingAuthReturn("user-a", 2_000)?.url).toBe(
      "/pages/positions/detail/index?positionId=p-1",
    );
    expect(consumePendingAuthReturn("user-a", 2_000)).toBeNull();
  });

  it("removes expired, tampered, and all versioned return records", () => {
    storePendingAuthReturn("user-a", "/pages/positions/detail/index?positionId=p-1", 1_000);
    expect(loadPendingAuthReturn("user-a", 1_000 + AUTH_RETURN_TTL_MS)).toBeNull();

    storage.set("gongkao.navigation.auth-return.v1.user-a", {
      schemaVersion: 1,
      ownerUserId: "user-a",
      createdAtMs: 1_000,
      expiresAtMs: 10_000,
      url: "/pages/login/index",
    });
    expect(loadPendingAuthReturn("user-a", 2_000)).toBeNull();

    storePendingAuthReturn("user-a", "/pages/positions/index", 20_000);
    storePendingAuthReturn("user-b", "/pages/today/index", 20_000);
    clearAllPendingAuthReturns();
    expect(storage.size).toBe(0);
  });
});
