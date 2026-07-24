import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearAuthSession,
  currentAuthSession,
  currentUserId,
  storeAuthSession,
} from "../miniprogram/auth/session";

let storage: Map<string, unknown>;
let globalData: { accessToken?: string; userId?: string };

beforeEach(() => {
  storage = new Map();
  globalData = {};
  vi.stubGlobal("getApp", () => ({ globalData }));
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => {
      storage.delete(key);
    },
  });
});

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("authenticated session identity", () => {
  it("persists the access token and stable user id as one session", () => {
    storeAuthSession("token-a", "user-a");
    globalData = {};

    expect(currentAuthSession()).toEqual({ accessToken: "token-a", userId: "user-a" });
    expect(currentUserId()).toBe("user-a");
    expect(globalData).toEqual({ accessToken: "token-a", userId: "user-a" });
  });

  it("clears both identity fields and the persisted session", () => {
    storeAuthSession("token-a", "user-a");

    clearAuthSession();

    expect(currentAuthSession()).toBeUndefined();
    expect(globalData).toEqual({});
    expect(storage.size).toBe(0);
  });

  it("rejects an auth response without a stable user id", () => {
    expect(() => {
      storeAuthSession("token-a", " ");
    }).toThrow("stable user");
    expect(storage.size).toBe(0);
  });
});
