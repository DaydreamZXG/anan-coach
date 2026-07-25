import { ApiClient } from "../../../api/client";
import { OnboardingApi } from "../../../api/onboarding";
import { routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  AbilityBaselineController,
  type AbilityBaselineControllerState,
} from "../../../features/onboarding/ability-baseline-controller";

interface AbilityBaselinePageData {
  state: AbilityBaselineControllerState | null;
  hasChoice: boolean;
  exitAfterSave: boolean;
}

interface AbilityBaselinePageCustom {
  selectMode(event: WechatMiniprogram.BaseEvent): void;
  updateScore(event: WechatMiniprogram.Input): void;
  submit(): void;
  saveAndExit(): void;
  retry(): void;
  back(): void;
}

type AbilityBaselinePage = WechatMiniprogram.Page.Instance<
  AbilityBaselinePageData,
  AbilityBaselinePageCustom
>;

const runtimes = new PageControllerRegistry<AbilityBaselinePage, AbilityBaselineController>();

Page<AbilityBaselinePageData, AbilityBaselinePageCustom>({
  data: {
    state: null,
    hasChoice: false,
    exitAfterSave: false,
  },

  onLoad() {
    mountController(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) mountController(this);
  },

  onUnload() {
    runtimes.detach(this);
  },

  selectMode(event) {
    const mode = String(event.currentTarget.dataset.mode ?? "");
    if (mode === "unknown" || mode === "reported_score") {
      runtimes.get(this)?.controller?.selectMode(mode);
    }
  },

  updateScore(event) {
    const field = String(event.currentTarget.dataset.field ?? "");
    if (field === "xingceScore" || field === "shenlunScore") {
      runtimes.get(this)?.controller?.setScore(field, event.detail.value);
    }
  },

  submit() {
    this.setData({ exitAfterSave: false });
    void runtimes.get(this)?.controller?.submit();
  },

  saveAndExit() {
    if (this.data.state?.saving) return;
    if (!this.data.hasChoice) {
      void wx.redirectTo({ url: routes.profileArchive });
      return;
    }
    this.setData({ exitAfterSave: true });
    void runtimes.get(this)?.controller?.submit();
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },

  back() {
    void wx.redirectTo({ url: routes.onboardingStudyPreference });
  },
});

function mountController(page: AbilityBaselinePage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const runtime = runtimes.attach(page);
  const controller = new AbilityBaselineController({
    userId: session.userId,
    api: new OnboardingApi(new ApiClient(undefined, session.accessToken)),
    onChange: (state) => {
      if (!runtimes.owns(page, runtime)) return;
      page.setData({
        state,
        hasChoice: state.draft.mode !== null,
      });
    },
    onAdvance: () => {
      if (runtimes.owns(page, runtime)) {
        if (page.data.exitAfterSave) {
          page.setData({ exitAfterSave: false });
          void wx.redirectTo({ url: routes.profileArchive });
          return;
        }
        void wx.redirectTo({ url: routes.onboardingResult });
      }
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  const state = controller.snapshot();
  page.setData({ state, hasChoice: false, exitAfterSave: false });
  void controller.load();
}
