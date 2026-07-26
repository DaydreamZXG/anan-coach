import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  goBack(this: TestPage): void;
}

interface TestPage extends CapturedPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

let captured: CapturedPageDefinition | null;
let navigateBack: ReturnType<typeof vi.fn>;
let switchTab: ReturnType<typeof vi.fn>;

beforeEach(() => {
  captured = null;
  navigateBack = vi.fn();
  switchTab = vi.fn();
  vi.stubGlobal("Page", (definition: CapturedPageDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData: {} }));
  vi.stubGlobal("wx", {
    getStorageSync: () => undefined,
    setStorageSync: () => {},
    removeStorageSync: () => {},
    navigateBack,
    switchTab,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("capture import back navigation fallback", () => {
  it("pops the stack when history exists and reroutes to today when it is the entry page", async () => {
    await import("../../miniprogram/pages/capture-import/index");
    if (!captured) throw new Error("Page definition was not captured");
    const page: TestPage = {
      ...captured,
      data: JSON.parse(JSON.stringify(captured.data)) as Record<string, unknown>,
      setData(patch: Record<string, unknown>) {
        Object.assign(this.data, patch);
      },
    };

    vi.stubGlobal("getCurrentPages", () => [{}, {}]);
    page.goBack.call(page);
    expect(navigateBack).toHaveBeenCalledTimes(1);
    expect(switchTab).not.toHaveBeenCalled();

    vi.stubGlobal("getCurrentPages", () => [{}]);
    page.goBack.call(page);
    expect(navigateBack).toHaveBeenCalledTimes(1);
    expect(switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
  });

  it("keeps the layered dismissals ahead of the stack fallback", async () => {
    await import("../../miniprogram/pages/capture-import/index");
    if (!captured) throw new Error("Page definition was not captured");
    const page: TestPage = {
      ...captured,
      data: JSON.parse(JSON.stringify(captured.data)) as Record<string, unknown>,
      setData(patch: Record<string, unknown>) {
        Object.assign(this.data, patch);
      },
    };
    vi.stubGlobal("getCurrentPages", () => [{}]);

    page.setData({ receiptVisible: true });
    page.goBack.call(page);
    expect(page.data.receiptVisible).toBe(false);
    expect(navigateBack).not.toHaveBeenCalled();
    expect(switchTab).not.toHaveBeenCalled();

    page.goBack.call(page);
    expect(switchTab).toHaveBeenCalledWith({ url: "/pages/today/index" });
  });
});
