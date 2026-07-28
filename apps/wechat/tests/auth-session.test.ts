import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearAuthSession,
  currentAuthSession,
  currentUserId,
  storeAuthSession,
  updateAuthDisplayName,
} from "../miniprogram/auth/session";

let storage: Map<string, unknown>;
let globalData: {
  accessToken?: string;
  userId?: string;
  displayName?: string;
  generation?: string;
};

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

    expect(currentAuthSession()).toMatchObject({ accessToken: "token-a", userId: "user-a" });
    expect(currentAuthSession()?.generation).toMatch(/^session-/u);
    expect(currentUserId()).toBe("user-a");
    expect(globalData).toMatchObject({ accessToken: "token-a", userId: "user-a" });
    expect(globalData.generation).toMatch(/^session-/u);
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

  it("upgrades a persisted v1 session once and retains its generation on future reads", () => {
    storage.set("gongkao.auth.session.v1", {
      accessToken: "token-legacy",
      userId: "user-legacy",
      displayName: "旧用户",
    });

    const upgraded = currentAuthSession();
    const persisted = storage.get("gongkao.auth.session.v1");
    globalData = {};
    const reread = currentAuthSession();

    expect(upgraded).toMatchObject({
      accessToken: "token-legacy",
      userId: "user-legacy",
      displayName: "旧用户",
    });
    expect(upgraded?.generation).toMatch(/^session-/u);
    expect(persisted).toEqual(upgraded);
    expect(reread).toEqual(upgraded);
  });

  it("upgrades a legacy global session once and persists its fixed generation", () => {
    globalData = {
      accessToken: "token-global",
      userId: "user-global",
      displayName: "  旧 用户  ",
    };

    const upgraded = currentAuthSession();
    const reread = currentAuthSession();

    expect(upgraded).toMatchObject({
      accessToken: "token-global",
      userId: "user-global",
      displayName: "旧 用户",
    });
    expect(upgraded?.generation).toMatch(/^session-/u);
    expect(reread).toEqual(upgraded);
    expect(globalData.generation).toBe(upgraded?.generation);
    expect(storage.get("gongkao.auth.session.v1")).toEqual(upgraded);
  });

  it("keeps one generated legacy-global generation when storage APIs are unavailable", () => {
    vi.stubGlobal("wx", {});
    globalData = { accessToken: "token-memory", userId: "user-memory" };

    const first = currentAuthSession();
    const second = currentAuthSession();

    expect(first?.generation).toMatch(/^session-/u);
    expect(second).toEqual(first);
    expect(globalData.generation).toBe(first?.generation);
  });

  it("reuses a matching persisted generation while upgrading legacy global data", () => {
    storage.set("gongkao.auth.session.v1", {
      accessToken: "token-a",
      userId: "user-a",
      displayName: "持久用户",
      generation: "session-persisted",
    });
    globalData = { accessToken: "token-a", userId: "user-a" };

    expect(currentAuthSession()).toEqual({
      accessToken: "token-a",
      userId: "user-a",
      displayName: "持久用户",
      generation: "session-persisted",
    });
    expect(globalData.generation).toBe("session-persisted");
  });

  it.each([
    ["token only", { accessToken: "token-a" }],
    ["user only", { userId: "user-a" }],
    ["blank generation", { accessToken: "token-a", userId: "user-a", generation: "   " }],
  ])("fails closed for a malformed global session: %s", (_label, malformed) => {
    storage.set("gongkao.auth.session.v1", {
      accessToken: "persisted-token",
      userId: "persisted-user",
      generation: "session-persisted",
    });
    globalData = malformed;

    expect(currentAuthSession()).toBeUndefined();
  });

  it("does not upgrade a persisted session with a malformed generation", () => {
    storage.set("gongkao.auth.session.v1", {
      accessToken: "token-a",
      userId: "user-a",
      generation: "",
    });

    expect(currentAuthSession()).toBeUndefined();
    expect(storage.has("gongkao.auth.session.v1")).toBe(false);
  });

  it("preserves the generation when only the display name changes", () => {
    storeAuthSession("token-a", "user-a", "原名称");
    const generation = currentAuthSession()?.generation;

    updateAuthDisplayName("新名称");

    expect(currentAuthSession()).toMatchObject({
      accessToken: "token-a",
      userId: "user-a",
      displayName: "新名称",
      generation,
    });
  });
});
