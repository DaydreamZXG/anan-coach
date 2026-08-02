import { describe, expect, it, vi } from "vitest";

import { ApiClientError } from "../miniprogram/api/client";
import type { WechatAuthSessionData } from "../miniprogram/api/generated/types.gen";
import {
  WechatLoginController,
  presentWechatLoginError,
  type WechatLoginDependencies,
  type WechatLoginState,
} from "../miniprogram/features/identity/wechat-login-controller";

describe("wechat login controller", () => {
  it("commits one validated account session and follows the server-owned next step", async () => {
    const harness = createHarness();

    await harness.controller.login();

    expect(harness.spies.requestCode).toHaveBeenCalledTimes(1);
    expect(harness.spies.authenticate).toHaveBeenCalledWith({
      code: "wechat-code",
      deviceId: "device-1",
    });
    expect(harness.spies.commitSession).toHaveBeenCalledWith(session());
    expect(harness.spies.navigate).toHaveBeenCalledWith("preparation_experience");
    expect(harness.states.at(-1)).toEqual({
      restoring: false,
      submitting: false,
      errorCode: "",
      errorMessage: "",
    });
  });

  it("restores a persisted session from the server-owned onboarding progress", async () => {
    const progress = session().onboardingProgress;
    const harness = createHarness({ restoreProgress: vi.fn(() => Promise.resolve(progress)) });

    await harness.controller.resume();

    expect(harness.spies.restoreProgress).toHaveBeenCalledTimes(1);
    expect(harness.spies.authenticate).not.toHaveBeenCalled();
    expect(harness.spies.requestCode).not.toHaveBeenCalled();
    expect(harness.spies.navigate).toHaveBeenCalledWith("preparation_experience");
    expect(harness.states.at(-1)).toEqual({
      restoring: false,
      submitting: false,
      errorCode: "",
      errorMessage: "",
    });
  });

  it("removes only an authenticated session rejected by the server", async () => {
    const harness = createHarness({
      restoreProgress: vi.fn(() =>
        Promise.reject(
          new ApiClientError("登录失效", 401, "restore-request", {
            code: "authentication_required",
            message: "登录失效",
            requestId: "restore-request",
          }),
        ),
      ),
    });

    await harness.controller.resume();

    expect(harness.spies.discardSession).toHaveBeenCalledTimes(1);
    expect(harness.states.at(-1)).toMatchObject({
      restoring: false,
      errorCode: "stored_session_expired",
    });
  });

  it("keeps a persisted session when recovery fails for a transient network reason", async () => {
    const harness = createHarness({
      restoreProgress: vi.fn(() =>
        Promise.reject(new ApiClientError("网络请求失败", 0, "restore-network")),
      ),
    });

    await harness.controller.resume();

    expect(harness.spies.discardSession).not.toHaveBeenCalled();
    const state = harness.states.at(-1);
    expect(state?.restoring).toBe(false);
    expect(state?.errorCode).toBe("session_restore_failed");
    expect(state?.errorMessage).toContain("restore-network");
  });

  it("keeps a persisted session when recovery fails with a temporary server error", async () => {
    const harness = createHarness({
      restoreProgress: vi.fn(() =>
        Promise.reject(
          new ApiClientError("服务暂时不可用", 503, "restore-upstream", {
            code: "service_unavailable",
            message: "服务暂时不可用",
            requestId: "restore-upstream",
          }),
        ),
      ),
    });

    await harness.controller.resume();

    expect(harness.spies.discardSession).not.toHaveBeenCalled();
    expect(harness.states.at(-1)).toMatchObject({
      restoring: false,
      errorCode: "service_unavailable",
    });
  });

  it("allows a new command after a typed login failure has settled", async () => {
    const authenticate = vi
      .fn(() => Promise.resolve(session()))
      .mockRejectedValueOnce(
        new ApiClientError("请求频繁", 429, "login-rate-limit", {
          code: "wechat_login_rate_limited",
          message: "请求频繁",
          requestId: "login-rate-limit",
        }),
      );
    const harness = createHarness({ authenticate });

    await harness.controller.login();
    await harness.controller.login();

    expect(authenticate).toHaveBeenCalledTimes(2);
    expect(harness.spies.commitSession).toHaveBeenCalledTimes(1);
    expect(harness.spies.navigate).toHaveBeenCalledTimes(1);
  });

  it("shares one in-flight operation across repeated taps", async () => {
    const pending = deferred<WechatAuthSessionData>();
    const harness = createHarness({ authenticate: vi.fn(() => pending.promise) });

    const first = harness.controller.login();
    const second = harness.controller.login();
    await Promise.resolve();

    expect(first).toBe(second);
    expect(harness.spies.requestCode).toHaveBeenCalledTimes(1);
    pending.resolve(session());
    await first;
    expect(harness.spies.commitSession).toHaveBeenCalledTimes(1);
  });

  it("does not write session, navigation, or page state after page disposal", async () => {
    const pending = deferred<WechatAuthSessionData>();
    const harness = createHarness({ authenticate: vi.fn(() => pending.promise) });
    const operation = harness.controller.login();
    await Promise.resolve();
    const stateCount = harness.states.length;

    harness.controller.dispose();
    pending.resolve(session());
    await operation;

    expect(harness.spies.commitSession).not.toHaveBeenCalled();
    expect(harness.spies.navigate).not.toHaveBeenCalled();
    expect(harness.states).toHaveLength(stateCount);
  });

  it("rejects contradictory onboarding owners before persisting credentials", async () => {
    const harness = createHarness({
      authenticate: vi.fn(() =>
        Promise.resolve(session({ onboardingComplete: true, completed: false })),
      ),
    });

    await harness.controller.login();

    expect(harness.spies.commitSession).not.toHaveBeenCalled();
    expect(harness.spies.navigate).not.toHaveBeenCalled();
    expect(harness.states.at(-1)).toMatchObject({
      submitting: false,
      errorCode: "wechat_login_contract_invalid",
    });
  });

  it("rejects an incomplete profile that falsely points at the workspace", async () => {
    const invalid = session({ onboardingComplete: false, completed: false });
    invalid.onboardingProgress.nextStep = "workspace";
    const harness = createHarness({
      authenticate: vi.fn(() => Promise.resolve(invalid)),
    });

    await harness.controller.login();

    expect(harness.spies.commitSession).not.toHaveBeenCalled();
    expect(harness.spies.navigate).not.toHaveBeenCalled();
    expect(harness.states.at(-1)).toMatchObject({
      submitting: false,
      errorCode: "wechat_login_contract_invalid",
    });
  });

  it.each<[string, string]>([
    ["wechat_login_rate_limited", "登录操作有些频繁"],
    ["wechat_login_rejected", "登录凭证已失效"],
    ["wechat_login_upstream_unavailable", "登录服务暂时不可用"],
    ["account_deletion_pending", "账户正在注销流程中"],
    ["account_disabled", "账户当前不可用"],
  ])("maps %s to a stable user-facing state", (code, expected) => {
    const error = new ApiClientError("公开错误", 409, "request-1", {
      code,
      message: "公开错误",
      requestId: "request-1",
    });
    const view = presentWechatLoginError(error);
    expect(view.code).toBe(code);
    expect(view.message).toContain(expected);
  });

  it("keeps the request id for an unknown typed server failure", () => {
    const error = new ApiClientError("登录失败", 502, "request-unknown", {
      code: "future_login_failure",
      message: "登录失败",
      requestId: "request-unknown",
    });
    expect(presentWechatLoginError(error)).toEqual({
      code: "future_login_failure",
      message: "登录失败（请求 request-unknown）",
    });
  });
});

function createHarness(changes: Partial<WechatLoginDependencies> = {}) {
  const states: WechatLoginState[] = [];
  const requestCode = changes.requestCode ?? vi.fn(() => Promise.resolve("wechat-code"));
  const deviceId = changes.deviceId ?? vi.fn(() => "device-1");
  const authenticate = changes.authenticate ?? vi.fn(() => Promise.resolve(session()));
  const restoreProgress = changes.restoreProgress ?? vi.fn(() => Promise.resolve(null));
  const commitSession = changes.commitSession ?? vi.fn();
  const discardSession = changes.discardSession ?? vi.fn();
  const navigate = changes.navigate ?? vi.fn(() => Promise.resolve());
  const publish = changes.publish ?? vi.fn((state: WechatLoginState) => states.push({ ...state }));
  const dependencies: WechatLoginDependencies = {
    requestCode,
    deviceId,
    authenticate,
    restoreProgress,
    commitSession,
    discardSession,
    navigate,
    publish,
    ...changes,
  };
  return {
    controller: new WechatLoginController(dependencies),
    dependencies,
    states,
    spies: {
      requestCode,
      deviceId,
      authenticate,
      restoreProgress,
      commitSession,
      discardSession,
      navigate,
      publish,
    },
  };
}

function session(
  changes: { onboardingComplete?: boolean; completed?: boolean } = {},
): WechatAuthSessionData {
  const completed = changes.completed ?? false;
  return {
    accessToken: "access-token",
    expiresAt: "2026-07-15T00:00:00Z",
    onboardingComplete: changes.onboardingComplete ?? completed,
    onboardingProgress: {
      completed,
      completedAt: null,
      experienceProfileVersion: completed ? 1 : null,
      experienceReady: completed,
      nextStep: completed ? "workspace" : "preparation_experience",
      progressVersion: 1,
      qualificationProfileVersion: null,
      qualificationReady: false,
      resultAcknowledged: false,
      resultReady: false,
      studyPreferenceReady: false,
      studyPreferenceVersion: null,
      updatedAt: "2026-07-14T00:00:00Z",
    },
    tokenType: "bearer",
    user: {
      userId: "user-1",
      learnerKey: "learner-1",
      displayName: "测试用户",
      email: null,
    },
  };
}

function deferred<T>(): {
  promise: Promise<T>;
  resolve(value: T): void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}
