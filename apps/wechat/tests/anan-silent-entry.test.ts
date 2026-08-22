import { describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import entrySource from "../miniprogram/pages/login/index.ts?raw";
import { routes } from "../miniprogram/app/navigation/routes";
import {
  bootstrapSilentWechatSession,
  learningEvidenceStateFromCoachContext,
  SILENT_WECHAT_LEGAL_ACCEPTED,
  silentEntryErrorMessage,
  silentEntryNavigationTarget,
} from "../miniprogram/features/identity/silent-entry";

describe("岸岸静默微信入口", () => {
  it("已有会话只恢复，不重复发起微信登录", async () => {
    let hasSession = true;
    const controller = {
      resume: vi.fn(() => Promise.resolve()),
      login: vi.fn(() => {
        hasSession = true;
        return Promise.resolve();
      }),
    };

    await bootstrapSilentWechatSession(controller, () => hasSession);

    expect(controller.resume).toHaveBeenCalledTimes(1);
    expect(controller.login).not.toHaveBeenCalled();
  });

  it("新用户自动建立微信身份且不伪造条款接受", async () => {
    let hasSession = false;
    const controller = {
      resume: vi.fn(() => Promise.resolve()),
      login: vi.fn(() => {
        hasSession = true;
        return Promise.resolve();
      }),
    };

    await bootstrapSilentWechatSession(controller, () => hasSession);

    expect(controller.login).toHaveBeenCalledTimes(1);
    expect(hasSession).toBe(true);
    expect(SILENT_WECHAT_LEGAL_ACCEPTED).toBe(false);
    expect(entrySource).toContain("currentAuthSession() !== undefined");
    expect(entrySource).not.toContain("currentAuthSession() !== null");
    expect(entrySource).not.toContain("toggleLegalConsent");
  });

  it("失效会话被恢复流程清除后自动重新认证", async () => {
    let hasSession = true;
    const controller = {
      resume: vi.fn(() => {
        hasSession = false;
        return Promise.resolve();
      }),
      login: vi.fn(() => {
        hasSession = true;
        return Promise.resolve();
      }),
    };

    await bootstrapSilentWechatSession(controller, () => hasSession);

    expect(controller.resume).toHaveBeenCalledTimes(1);
    expect(controller.login).toHaveBeenCalledTimes(1);
    expect(hasSession).toBe(true);
  });

  it("瞬时恢复失败保留会话时不创建第二个登录", async () => {
    const controller = {
      resume: vi.fn(() => Promise.resolve()),
      login: vi.fn(() => Promise.resolve()),
    };

    await bootstrapSilentWechatSession(controller, () => true);

    expect(controller.login).not.toHaveBeenCalled();
  });

  it("新用户继续建档，老用户进入今日并保留安全返回目标", () => {
    expect(silentEntryNavigationTarget("preparation_experience", null)).toEqual({
      mode: "navigateTo",
      url: routes.onboardingEntry,
    });
    expect(silentEntryNavigationTarget("qualification", null).url).toBe(routes.onboarding);
    expect(silentEntryNavigationTarget("study_preference", null).url).toBe(
      routes.onboardingPositionPreference,
    );
    expect(silentEntryNavigationTarget("result", null).url).toBe(routes.onboardingAbilityBaseline);

    const pending = { mode: "navigateTo" as const, url: routes.positionSearch };
    expect(silentEntryNavigationTarget("workspace", pending)).toBe(pending);
    expect(silentEntryNavigationTarget("workspace", null)).toEqual({
      mode: "switchTab",
      url: routes.today,
    });
  });

  it("已有真实学习证据但旧建档未完成时直接进入今日", () => {
    expect(silentEntryNavigationTarget("preparation_experience", null, true)).toEqual({
      mode: "switchTab",
      url: routes.today,
    });

    const pending = { mode: "navigateTo" as const, url: routes.positionSearch };
    expect(silentEntryNavigationTarget("preparation_experience", pending, true)).toBe(pending);
  });

  it("岸岸认识投影暂时失败时不把未知证据冒充老用户证据", () => {
    expect(silentEntryNavigationTarget("preparation_experience", null, null)).toEqual({
      mode: "navigateTo",
      url: routes.onboardingEntry,
    });
  });

  it("岸岸认识投影缺字段时返回未知，不因运行时结构不完整而崩溃", () => {
    expect(learningEvidenceStateFromCoachContext(undefined)).toBeNull();
    expect(learningEvidenceStateFromCoachContext({})).toBeNull();
    expect(learningEvidenceStateFromCoachContext({ recognition: {} })).toBeNull();
    expect(
      learningEvidenceStateFromCoachContext({ recognition: { evidenceCount: Number.NaN } }),
    ).toBeNull();
    expect(learningEvidenceStateFromCoachContext({ recognition: { evidenceCount: 0 } })).toBe(
      false,
    );
    expect(learningEvidenceStateFromCoachContext({ recognition: { evidenceCount: 2 } })).toBe(true);
  });

  it("恢复失败只提示重新进入岸岸，不重新制造登录产品门槛", () => {
    expect(silentEntryErrorMessage("暂时无法恢复上次登录，请重新微信登录。")).toBe(
      "暂时无法恢复上次连接，请重新进入岸岸。",
    );
  });
});
