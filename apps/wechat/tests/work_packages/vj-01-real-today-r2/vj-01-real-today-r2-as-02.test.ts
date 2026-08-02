import { afterEach, describe, expect, it, vi } from "vitest";

import type { WechatAuthSessionData } from "../../../miniprogram/api/generated/types.gen";
import { WechatLoginController } from "../../../miniprogram/features/identity/wechat-login-controller";
import { createTodayHarness, incompleteProgress, latestState } from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-01 real Today R2 AS-02", () => {
  it("wires a direct Today page through progress and never requests a plan when incomplete", async () => {
    let captured: unknown;
    const requests: WechatMiniprogram.RequestOption[] = [];
    const reLaunch = vi.fn(() => Promise.resolve());
    vi.stubGlobal("Page", (definition: unknown) => {
      captured = definition;
    });
    vi.stubGlobal("getApp", () => ({
      globalData: { accessToken: "server-token-a", userId: "user-a" },
    }));
    vi.stubGlobal("wx", {
      getStorageSync: () => "",
      removeStorageSync: vi.fn(),
      reLaunch,
      request: (options: WechatMiniprogram.RequestOption) => requests.push(options),
    });

    await import("../../../miniprogram/pages/today/index");
    const page = instantiatePage(captured as CapturedTodayPage);
    page.onLoad.call(page);
    page.onShow.call(page);
    expect(requests).toHaveLength(1);
    expect(requests[0]?.url).toBe("https://kg.xingguoxian.cn/staging/api/v2/onboarding/progress");
    requests[0]?.success?.(success(incompleteProgress("qualification")));
    await flushPromises();

    expect(requests).toHaveLength(1);
    expect(reLaunch).toHaveBeenCalledWith({ url: "/pages/onboarding/index" });
    page.onUnload.call(page);
  });

  it("routes a direct Today visit through server-owned onboarding before reading a plan", async () => {
    const progress = incompleteProgress("qualification");
    const harness = createTodayHarness({
      getProgress: vi.fn(() => Promise.resolve(progress)),
    });

    await harness.controller.load();

    expect(harness.spies.getProgress).toHaveBeenCalledWith("server-token-a");
    expect(harness.spies.getToday).not.toHaveBeenCalled();
    expect(harness.spies.navigateOnboarding).toHaveBeenCalledWith("qualification");
    expect(harness.spies.clearSession).not.toHaveBeenCalled();
    expect(latestState(harness.states)).toMatchObject({
      status: "redirecting",
      dashboard: null,
      capacitySubmission: "idle",
    });
  });

  it("never restores an old plan after the server closes the onboarding gate", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();

    harness.spies.getProgress.mockResolvedValueOnce(incompleteProgress("study_preference"));
    harness.spies.navigateOnboarding.mockRejectedValueOnce(new Error("reLaunch failed"));
    await harness.controller.load();

    expect(harness.spies.getToday).toHaveBeenCalledOnce();
    expect(harness.spies.navigateOnboarding).toHaveBeenCalledWith("study_preference");
    expect(latestState(harness.states)).toMatchObject({
      status: "error",
      dashboard: null,
      capacitySubmission: "idle",
      capacityConflictLocked: false,
    });
    expect(latestState(harness.states).errorMessage).toContain("今日计划仍保持锁定");
  });

  it("still performs real authentication before following an incomplete next step", async () => {
    const authenticate = vi.fn(() => Promise.resolve(loginSession()));
    const commitSession = vi.fn();
    const navigate = vi.fn(() => Promise.resolve());
    const requestCode = vi.fn(() => Promise.resolve("real-wechat-code"));
    const controller = new WechatLoginController({
      requestCode,
      deviceId: () => "wechat-device",
      authenticate,
      restoreProgress: () => Promise.resolve(null),
      commitSession,
      discardSession: vi.fn(),
      navigate,
      publish: vi.fn(),
    });

    await controller.login();

    expect(requestCode).toHaveBeenCalledOnce();
    expect(authenticate).toHaveBeenCalledWith({
      code: "real-wechat-code",
      deviceId: "wechat-device",
    });
    expect(commitSession).toHaveBeenCalledOnce();
    expect(navigate).toHaveBeenCalledWith("qualification");
  });
});

function loginSession(): WechatAuthSessionData {
  return {
    accessToken: "new-server-session",
    expiresAt: "2026-08-15T00:00:00Z",
    onboardingComplete: false,
    onboardingProgress: incompleteProgress("qualification"),
    tokenType: "bearer",
    user: {
      userId: "new-user",
      learnerKey: "new-learner",
      displayName: "微信用户",
      email: null,
    },
  };
}

interface CapturedTodayPage {
  data: Record<string, unknown>;
  onLoad(this: TestTodayPage): void;
  onShow(this: TestTodayPage): void;
  onUnload(this: TestTodayPage): void;
}

interface TestTodayPage extends CapturedTodayPage {
  setData(patch: Record<string, unknown>): void;
}

function instantiatePage(definition: CapturedTodayPage): TestTodayPage {
  return {
    ...definition,
    data: JSON.parse(JSON.stringify(definition.data)) as Record<string, unknown>,
    setData(patch) {
      Object.assign(this.data, patch);
    },
  };
}

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "progress-request",
      generatedAt: "2026-07-15T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}

async function flushPromises(): Promise<void> {
  for (let index = 0; index < 6; index += 1) await Promise.resolve();
}
