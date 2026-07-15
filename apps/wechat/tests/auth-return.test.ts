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
