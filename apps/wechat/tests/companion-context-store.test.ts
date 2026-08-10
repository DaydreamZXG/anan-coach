import { afterEach, describe, expect, it, vi } from "vitest";
import {
  consumeCompanionLaunchContext,
  storeCompanionLaunchContext,
} from "../miniprogram/features/companion/context-store";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("companion launch context", () => {
  it("accepts registered subpackage routes and preserves exact object versions", () => {
    const storage = installStorage();
    vi.spyOn(Date, "now").mockReturnValue(1_000);
    storeCompanionLaunchContext("user-a", {
      sourceLabel: "错误模式详情",
      title: "基期量误读",
      summary: "已出现 4 次",
      suggestedQuestion: "帮我讲清触发条件",
      routeId: "/reports/error-pattern/index",
      workspace: "profile",
      objectRefs: [
        {
          type: "error_pattern",
          id: "a".repeat(64),
          version: "00000000-0000-4000-8000-00000000e201",
        },
      ],
    });

    const stored = [...storage.values()][0] as {
      context: { routeId: string };
    };
    expect(stored.context.routeId).toBe("reports/error-pattern/index");
    expect(consumeCompanionLaunchContext("user-a")?.objectRefs).toEqual([
      {
        type: "error_pattern",
        id: "a".repeat(64),
        version: "00000000-0000-4000-8000-00000000e201",
      },
    ]);
    expect(consumeCompanionLaunchContext("user-a")).toBeNull();
  });

  it("keeps daily settlement context account-scoped and expires it", () => {
    installStorage();
    vi.spyOn(Date, "now").mockReturnValue(10_000);
    storeCompanionLaunchContext("user-a", {
      sourceLabel: "今日学习结算",
      title: "8 月 10 日",
      summary: "完成 2 项",
      suggestedQuestion: "明天先做什么？",
      routeId: "plans/daily-settlement/index",
      workspace: "today",
      objectRefs: [
        {
          type: "plan",
          id: "00000000-0000-4000-8000-00000000e301",
          version: "3",
        },
      ],
    });

    expect(consumeCompanionLaunchContext("user-b")).toBeNull();
    vi.spyOn(Date, "now").mockReturnValue(10_000 + 31 * 60 * 1_000);
    expect(consumeCompanionLaunchContext("user-a")).toBeNull();
  });

  it("rejects traversal and incomplete task scope", () => {
    installStorage();
    expect(() => {
      storeCompanionLaunchContext("user-a", {
        sourceLabel: "来源",
        title: "标题",
        summary: "摘要",
        suggestedQuestion: "问题",
        routeId: "reports/../private/index",
        workspace: "profile",
        objectRefs: [],
      });
    }).toThrow("route is invalid");
    expect(() => {
      storeCompanionLaunchContext("user-a", {
        sourceLabel: "来源",
        title: "标题",
        summary: "摘要",
        suggestedQuestion: "问题",
        routeId: "pages/practice/index",
        workspace: "training",
        objectRefs: [],
        taskMode: "practice",
      });
    }).toThrow("mode and stage");
  });
});

function installStorage(): Map<string, unknown> {
  const storage = new Map<string, unknown>();
  vi.stubGlobal("wx", {
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    getStorageSync: (key: string) => storage.get(key),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  });
  return storage;
}
