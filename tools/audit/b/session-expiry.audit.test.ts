import { afterEach, describe, expect, it, vi } from "vitest";

interface CapturedPageDefinition {
  data: Record<string, unknown>;
  onLoad(this: TestPage, options: Record<string, string | undefined>): void;
  onShow?(this: TestPage): void;
  onUnload?(this: TestPage): void;
  retry(this: TestPage): void;
}

interface TestPage extends CapturedPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

interface AppState {
  globalData: {
    accessToken?: string;
    userId?: string;
    displayName?: string;
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("audit reproduction: core-flow session expiry", () => {
  it("leaves the essay retry action inert after a workspace request expires the session", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/essay-task/index",
    );
    const page = instantiatePage(harness.definition);

    page.onLoad.call(page, {
      taskVersionId: "essay-task-a",
      planTaskId: "plan-task-a",
      stage: "essay_material_reading",
    });
    page.onShow?.call(page);
    await flushMicrotasks();

    expect(harness.requests).toHaveLength(1);
    expect(page.data.status).toBe("error");
    expect(page.data.showLoginAction).toBe(false);
    expect(harness.appState.globalData.accessToken).toBeUndefined();

    page.retry.call(page);
    await flushMicrotasks();

    expect(harness.requests).toHaveLength(1);
    expect(harness.reLaunch).not.toHaveBeenCalled();
    expect(harness.navigateTo).not.toHaveBeenCalled();
    expect(page.data.status).toBe("error");
  });

  it("retries mock recovery with the expired token and stays on the same error state", async () => {
    const harness = await capturePage(
      "../../../apps/wechat/miniprogram/pages/mock-exam/index",
    );
    const page = instantiatePage(harness.definition);

    page.onLoad.call(page, {});
    page.onShow?.call(page);
    await flushMicrotasks();

    expect(harness.requests).toHaveLength(1);
    expect(page.data.status).toBe("error");
    expect(page.data.showLoginAction).toBe(false);
    expect(harness.appState.globalData.accessToken).toBeUndefined();

    page.retry.call(page);
    await flushMicrotasks();

    expect(harness.requests).toHaveLength(2);
    expect(
      harness.requests.map((request) => request.header?.Authorization),
    ).toEqual(["Bearer expired-token", "Bearer expired-token"]);
    expect(harness.reLaunch).not.toHaveBeenCalled();
    expect(harness.navigateTo).not.toHaveBeenCalled();
    expect(page.data.status).toBe("error");
  });
});

async function capturePage(modulePath: string): Promise<{
  definition: CapturedPageDefinition;
  requests: Array<{
    header?: Record<string, string>;
    success?(response: unknown): void;
  }>;
  appState: AppState;
  reLaunch: ReturnType<typeof vi.fn>;
  navigateTo: ReturnType<typeof vi.fn>;
}> {
  let captured: CapturedPageDefinition | undefined;
  const requests: Array<{
    header?: Record<string, string>;
    success?(response: unknown): void;
  }> = [];
  const appState: AppState = {
    globalData: {
      accessToken: "expired-token",
      userId: "user-a",
      displayName: "审计账号",
    },
  };
  const reLaunch = vi.fn(() => Promise.resolve());
  const navigateTo = vi.fn(() => Promise.resolve());

  vi.stubGlobal("Page", (definition: CapturedPageDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => appState);
  vi.stubGlobal("wx", {
    getStorageSync: () => undefined,
    setStorageSync: vi.fn(),
    removeStorageSync: vi.fn(),
    getStorageInfoSync: () => ({ keys: [], currentSize: 0, limitSize: 10_240 }),
    getRandomValues: () =>
      Promise.resolve({ randomValues: new Uint8Array(16).fill(7).buffer }),
    request: (options: {
      header?: Record<string, string>;
      success?(response: unknown): void;
    }) => {
      requests.push(options);
      options.success?.({
        statusCode: 401,
        data: {
          code: "authentication_required",
          message: "登录状态已失效。",
          requestId: "request-auth-expired",
        },
      });
    },
    reLaunch,
    navigateTo,
    redirectTo: vi.fn(() => Promise.resolve()),
    navigateBack: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    showToast: vi.fn(() => Promise.resolve()),
    pageScrollTo: vi.fn(() => Promise.resolve()),
  });

  const { installUnauthorizedReset } = await import(
    "../../../apps/wechat/miniprogram/api/auth"
  );
  installUnauthorizedReset();
  await import(modulePath);
  if (!captured) throw new Error(`Page was not registered: ${modulePath}`);
  return { definition: captured, requests, appState, reLaunch, navigateTo };
}

function instantiatePage(definition: CapturedPageDefinition): TestPage {
  return {
    ...definition,
    data: clone(definition.data),
    setData(patch: Record<string, unknown>) {
      Object.assign(this.data, patch);
    },
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

async function flushMicrotasks(turns = 20): Promise<void> {
  for (let index = 0; index < turns; index += 1) await Promise.resolve();
}
