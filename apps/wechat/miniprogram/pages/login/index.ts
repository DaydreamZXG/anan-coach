import { ApiClient } from "../../api/client";
import { clearSession, storeWechatSession } from "../../api/auth";
import type { WechatAuthSessionData } from "../../api/generated/types.gen";
import { OnboardingApi } from "../../api/onboarding";
import {
  clearPendingAuthReturn,
  loadPendingAuthReturn,
  loginReturnTarget,
  storePendingAuthReturn,
} from "../../app/navigation/auth-return";
import {
  onboardingNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { currentAuthSession, currentDeviceId } from "../../auth/session";
import { clearLegacyEssayPending } from "../../features/essay-task/pending-store";
import {
  WechatLoginController,
  type WechatLoginState,
} from "../../features/identity/wechat-login-controller";
import { clearOnboardingPendingForUser } from "../../features/onboarding/pending-store";

const controllers = new PageControllerRegistry<object, WechatLoginController>();

function loginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => {
        if (result.code) resolve(result.code);
        else reject(new Error("Wechat login returned no code"));
      },
      fail: () => {
        reject(new Error("Wechat login failed"));
      },
    });
  });
}

Page({
  data: {
    submitting: false,
    restoring: false,
    legalAccepted: false,
    errorCode: "",
    errorMessage: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const runtime = controllers.attach(this);
    runtime.controller = createController(this, loginReturnTarget(options.returnTo));
    void runtime.controller.resume();
  },

  onUnload() {
    controllers.detach(this);
  },

  login() {
    if (!this.data.legalAccepted) {
      void wx.showToast({ title: "请先阅读并同意用户协议与隐私保护指引", icon: "none" });
      return;
    }
    void controllers.get(this)?.controller?.login();
  },

  toggleLegalConsent(event: WechatMiniprogram.CheckboxGroupChange) {
    this.setData({ legalAccepted: event.detail.value.includes("accepted") });
  },

  openTerms() {
    void wx.navigateTo({ url: routes.terms });
  },

  openPrivacy() {
    wx.openPrivacyContract({
      fail: () => {
        void wx.showToast({ title: "隐私保护指引暂时无法打开，请稍后重试", icon: "none" });
      },
    });
  },
});

function createController(
  page: {
    setData(state: Partial<WechatLoginState>): void;
    data: { legalAccepted: boolean };
  },
  requestedReturn: RegisteredNavigationTarget | null,
) {
  const client = new ApiClient();
  return new WechatLoginController({
    requestCode: loginCode,
    deviceId: currentDeviceId,
    legalAccepted: () => page.data.legalAccepted,
    authenticate: async (request) =>
      (await client.post<WechatAuthSessionData>("/api/v2/auth/wechat", request)).data,
    restoreProgress: async () => {
      const existing = currentAuthSession();
      if (!existing) return null;
      return (await new OnboardingApi(new ApiClient(undefined, existing.accessToken)).getProgress())
        .data;
    },
    commitSession: (session) => {
      clearLegacyEssayPending();
      storeWechatSession(session);
      if (session.onboardingProgress.completed) {
        clearOnboardingPendingForUser(session.user.userId);
      }
    },
    discardSession: () => {
      const existing = currentAuthSession();
      if (existing) clearPendingAuthReturn(existing.userId);
      clearSession();
    },
    navigate: async (nextStep) => {
      const session = currentAuthSession();
      if (requestedReturn && session) {
        storePendingAuthReturn(session.userId, requestedReturn.url);
      }
      const pendingReturn = session ? loadPendingAuthReturn(session.userId) : requestedReturn;
      const target =
        nextStep === "workspace" && pendingReturn
          ? pendingReturn
          : onboardingNavigationTarget(nextStep);
      await navigateFromLogin(target);
      if (nextStep === "workspace" && pendingReturn && session) {
        clearPendingAuthReturn(session.userId);
      }
    },
    publish: (state) => {
      page.setData(state);
    },
  });
}

async function navigateFromLogin(target: RegisteredNavigationTarget): Promise<void> {
  if (target.mode === "switchTab") {
    await wx.switchTab({ url: target.url });
    return;
  }
  await wx.redirectTo({ url: target.url });
}
