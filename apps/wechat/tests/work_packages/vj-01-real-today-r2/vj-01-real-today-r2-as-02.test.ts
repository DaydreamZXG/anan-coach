import { afterEach, describe, expect, it, vi } from "vitest";

import type { WechatAuthSessionData } from "../../../miniprogram/api/generated/types.gen";
import { WechatLoginController } from "../../../miniprogram/features/identity/wechat-login-controller";
import { createTodayHarness, incompleteProgress, latestState, serverDashboard } from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-01 real Today R2 AS-02", () => {
  it("reads the real Today plan without turning incomplete onboarding into a gate", async () => {
    const progress = incompleteProgress("qualification");
    const harness = createTodayHarness({
      getProgress: vi.fn(() => Promise.resolve(progress)),
    });

    await harness.controller.load();

    expect(harness.spies.getProgress).toHaveBeenCalledWith("server-token-a");
    expect(harness.spies.getToday).toHaveBeenCalledWith("server-token-a");
    expect(harness.spies.navigateOnboarding).not.toHaveBeenCalled();
    expect(harness.spies.clearSession).not.toHaveBeenCalled();
    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: serverDashboard(),
      capacitySubmission: "idle",
    });
  });

  it("keeps refreshing Today even when the optional profile is still incomplete", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();

    harness.spies.getProgress.mockResolvedValueOnce(incompleteProgress("study_preference"));
    harness.spies.navigateOnboarding.mockRejectedValueOnce(new Error("reLaunch failed"));
    harness.spies.getToday.mockResolvedValueOnce(
      serverDashboard("server-plan-version-2", "capacity-version-2"),
    );
    await harness.controller.load();

    expect(harness.spies.getToday).toHaveBeenCalledTimes(2);
    expect(harness.spies.getProgress).toHaveBeenCalledTimes(2);
    expect(harness.spies.navigateOnboarding).not.toHaveBeenCalled();
    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: serverDashboard("server-plan-version-2", "capacity-version-2"),
      capacitySubmission: "idle",
      capacityConflictLocked: false,
    });
  });

  it("still performs real authentication before following an incomplete next step", async () => {
    const authenticate = vi.fn(() => Promise.resolve(loginSession()));
    const commitSession = vi.fn();
    const navigate = vi.fn(() => Promise.resolve());
    const requestCode = vi.fn(() => Promise.resolve("real-wechat-code"));
    const controller = new WechatLoginController({
      requestCode,
      deviceId: () => "wechat-device",
      legalAccepted: () => true,
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
      legalAccepted: true,
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
