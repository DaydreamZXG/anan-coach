import { ApiClient } from "../../../api/client";
import { OnboardingApi } from "../../../api/onboarding";
import { clearPendingAuthReturn, loadPendingAuthReturn } from "../../../app/navigation/auth-return";
import {
  onboardingNavigationTarget,
  onboardingResultActionTarget,
  routes,
} from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  OnboardingResultController,
  type OnboardingResultNavigation,
  type OnboardingResultControllerState,
} from "../../../features/onboarding/result-controller";

interface OnboardingResultPageData {
  state: OnboardingResultControllerState | null;
}

interface OnboardingResultPageCustom {
  acknowledge(): void;
  openArchive(): void;
  retry(): void;
}

type OnboardingResultPage = WechatMiniprogram.Page.Instance<
  OnboardingResultPageData,
  OnboardingResultPageCustom
>;

const runtimes = new PageControllerRegistry<OnboardingResultPage, OnboardingResultController>();

Page<OnboardingResultPageData, OnboardingResultPageCustom>({
  data: { state: null },

  onLoad() {
    mountController(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) mountController(this);
  },

  onUnload() {
    runtimes.detach(this);
  },

  acknowledge() {
    void runtimes.get(this)?.controller?.acknowledge();
  },

  openArchive() {
    void wx.navigateTo({ url: routes.profileArchive });
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },
});

function mountController(page: OnboardingResultPage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const runtime = runtimes.attach(page);
  const controller = new OnboardingResultController({
    userId: session.userId,
    api: new OnboardingApi(new ApiClient(undefined, session.accessToken)),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) page.setData({ state });
    },
    onAdvance: (navigation) => {
      if (runtimes.owns(page, runtime)) {
        navigateResult(
          navigation,
          session.userId,
          page.data.state?.result?.primaryGoal?.positionId,
        );
      }
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  page.setData({ state: controller.snapshot() });
  void controller.load();
}

function navigateResult(
  navigation: OnboardingResultNavigation,
  userId: string,
  positionId?: string,
): void {
  if (navigation.kind === "action") {
    const pendingReturn = loadPendingAuthReturn(userId);
    if (pendingReturn) {
      const operation =
        pendingReturn.mode === "switchTab"
          ? wx.switchTab({ url: pendingReturn.url })
          : wx.redirectTo({ url: pendingReturn.url });
      void operation.then(() => {
        clearPendingAuthReturn(userId);
      });
      return;
    }
    const target = onboardingResultActionTarget(navigation.action, positionId);
    if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
    else void wx.redirectTo({ url: target.url });
    return;
  }
  if (navigation.step === "result") return;
  const target = onboardingNavigationTarget(navigation.step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
