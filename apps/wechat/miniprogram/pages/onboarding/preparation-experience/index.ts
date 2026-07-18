import { ApiClient } from "../../../api/client";
import { OnboardingApi } from "../../../api/onboarding";
import { onboardingNavigationTarget, routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  PreparationExperienceController,
  type PreparationExperienceControllerState,
} from "../../../features/onboarding/preparation-experience-controller";
import type { PreparationExperienceChoice } from "../../../features/onboarding/preparation-experience";
import type { OnboardingNextStep } from "../../../features/onboarding/types";

interface PreparationExperiencePageData {
  state: PreparationExperienceControllerState | null;
}

interface PreparationExperiencePageCustom {
  selectChoice(event: WechatMiniprogram.BaseEvent): void;
  submit(): void;
  retry(): void;
}

type PreparationExperiencePage = WechatMiniprogram.Page.Instance<
  PreparationExperiencePageData,
  PreparationExperiencePageCustom
>;

const choices: PreparationExperienceChoice[] = [
  "firstSystematic",
  "externalLearning",
  "priorGuokaoExam",
  "retake",
];
const runtimes = new PageControllerRegistry<
  PreparationExperiencePage,
  PreparationExperienceController
>();

Page<PreparationExperiencePageData, PreparationExperiencePageCustom>({
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

  selectChoice(event) {
    const choice = String(event.currentTarget.dataset.choice ?? "");
    if (choices.includes(choice as PreparationExperienceChoice)) {
      runtimes.get(this)?.controller?.select(choice as PreparationExperienceChoice);
    }
  },

  submit() {
    void runtimes.get(this)?.controller?.submit();
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },
});

function mountController(page: PreparationExperiencePage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const runtime = runtimes.attach(page);
  const controller = new PreparationExperienceController({
    userId: session.userId,
    api: new OnboardingApi(new ApiClient(undefined, session.accessToken)),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) page.setData({ state });
    },
    onAdvance: (step) => {
      if (runtimes.owns(page, runtime)) navigateOnboardingStep(step);
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

function navigateOnboardingStep(step: OnboardingNextStep): void {
  if (step === "preparation_experience") return;
  const target = onboardingNavigationTarget(step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
