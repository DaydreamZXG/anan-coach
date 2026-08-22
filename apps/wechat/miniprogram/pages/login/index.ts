import { ApiClient } from "../../api/client";
import { clearSession, storeWechatSession } from "../../api/auth";
import type { WechatAuthSessionData } from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { OnboardingApi } from "../../api/onboarding";
import {
  clearPendingAuthReturn,
  loadPendingAuthReturn,
  loginReturnTarget,
  storePendingAuthReturn,
} from "../../app/navigation/auth-return";
import { type RegisteredNavigationTarget } from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { currentAuthSession, currentDeviceId } from "../../auth/session";
import { clearLegacyEssayPending } from "../../features/essay-task/pending-store";
import {
  WechatLoginController,
  type WechatLoginState,
} from "../../features/identity/wechat-login-controller";
import {
  bootstrapSilentWechatSession,
  learningEvidenceStateFromCoachContext,
  SILENT_WECHAT_LEGAL_ACCEPTED,
  silentEntryErrorMessage,
  silentEntryNavigationTarget,
} from "../../features/identity/silent-entry";
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
    errorCode: "",
    errorMessage: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const runtime = controllers.attach(this);
    runtime.controller = createController(this, loginReturnTarget(options.returnTo));
    void bootstrapSilentWechatSession(runtime.controller, () => currentAuthSession() !== undefined);
  },

  onUnload() {
    controllers.detach(this);
  },

  retry() {
    const controller = controllers.get(this)?.controller;
    if (!controller) return;
    void bootstrapSilentWechatSession(controller, () => currentAuthSession() !== undefined);
  },
});

function createController(
  page: {
    setData(state: Partial<WechatLoginState>): void;
  },
  requestedReturn: RegisteredNavigationTarget | null,
) {
  const client = new ApiClient();
  return new WechatLoginController({
    requestCode: loginCode,
    deviceId: currentDeviceId,
    legalAccepted: () => SILENT_WECHAT_LEGAL_ACCEPTED,
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
      const hasLearningEvidence = session
        ? await loadLearningEvidenceState(session.accessToken, nextStep)
        : false;
      const target = silentEntryNavigationTarget(nextStep, pendingReturn, hasLearningEvidence);
      await navigateFromLogin(target);
      if (pendingReturn && session && target.url === pendingReturn.url) {
        clearPendingAuthReturn(session.userId);
      }
    },
    publish: (state) => {
      page.setData({
        ...state,
        errorMessage: silentEntryErrorMessage(state.errorMessage),
      });
    },
  });
}

async function loadLearningEvidenceState(
  accessToken: string,
  nextStep: WechatAuthSessionData["onboardingProgress"]["nextStep"],
): Promise<boolean | null> {
  if (nextStep === "workspace") return true;
  try {
    const { data } = await new LearningBrainApi(
      new ApiClient(undefined, accessToken),
    ).getCoachContext();
    return learningEvidenceStateFromCoachContext(data);
  } catch (error) {
    // Unknown evidence must not impersonate established learning history. A
    // genuinely new learner continues into the progressive first coach
    // meeting; completed users already carry nextStep=workspace.
    console.warn("[identity] coach recognition unavailable during silent entry", error);
    return null;
  }
}

async function navigateFromLogin(target: RegisteredNavigationTarget): Promise<void> {
  if (target.mode === "switchTab") {
    await wx.switchTab({ url: target.url });
    return;
  }
  await wx.redirectTo({ url: target.url });
}
