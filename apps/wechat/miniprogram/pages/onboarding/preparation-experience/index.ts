import { AccountApi } from "../../../api/account";
import { ApiClient } from "../../../api/client";
import type { AccountProfileUpdate } from "../../../api/generated/types.gen";
import { OnboardingApi } from "../../../api/onboarding";
import { onboardingNavigationTarget, routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession, updateAuthDisplayName } from "../../../auth/session";
import {
  PreparationExperienceController,
  type PreparationExperienceControllerState,
} from "../../../features/onboarding/preparation-experience-controller";
import type { PreparationExperienceChoice } from "../../../features/onboarding/preparation-experience";
import type { OnboardingNextStep } from "../../../features/onboarding/types";

interface PreparationExperiencePageData {
  state: PreparationExperienceControllerState | null;
  displayName: string;
  originalDisplayName: string;
  avatarInitial: string;
  avatarStyle: AccountProfileUpdate["avatarStyle"];
  accountLoading: boolean;
  profileSaving: boolean;
  profileError: string;
  exitAfterSave: boolean;
}

interface PreparationExperiencePageCustom {
  selectChoice(event: WechatMiniprogram.BaseEvent): void;
  selectExamMode(event: WechatMiniprogram.BaseEvent): void;
  selectCurrentStatus(event: WechatMiniprogram.BaseEvent): void;
  inputDisplayName(event: WechatMiniprogram.Input): void;
  submit(): void;
  saveAndExit(): void;
  back(): void;
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
  data: {
    state: null,
    displayName: "",
    originalDisplayName: "",
    avatarInitial: "岸",
    avatarStyle: "ocean",
    accountLoading: true,
    profileSaving: false,
    profileError: "",
    exitAfterSave: false,
  },

  onLoad() {
    mountController(this);
    void loadAccountProfile(this);
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

  selectExamMode(event) {
    const mode = String(event.currentTarget.dataset.mode ?? "");
    const controller = runtimes.get(this)?.controller;
    const draft = this.data.state?.draft;
    if (!controller || !draft) return;
    if (mode === "first" && !draft.firstSystematic) controller.select("firstSystematic");
    if (mode === "retake" && !draft.retake) controller.select("retake");
  },

  selectCurrentStatus(event) {
    const status = String(event.currentTarget.dataset.status ?? "");
    if (status === "parttime" || status === "fulltime" || status === "student") {
      runtimes.get(this)?.controller?.selectCurrentStatus(status);
    }
  },

  inputDisplayName(event) {
    const displayName = event.detail.value;
    this.setData({
      displayName,
      avatarInitial: Array.from(displayName.trim()).at(0) ?? "岸",
      profileError: "",
    });
  },

  submit() {
    this.setData({ exitAfterSave: false });
    void saveIdentityAndExperience(this);
  },

  saveAndExit() {
    this.setData({ exitAfterSave: true });
    void saveIdentityAndExperience(this);
  },

  back() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.redirectTo({ url: routes.onboardingEntry });
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
      if (!runtimes.owns(page, runtime)) return;
      if (page.data.exitAfterSave) {
        page.setData({ exitAfterSave: false });
        void wx.redirectTo({ url: routes.profileArchive });
        return;
      }
      navigateOnboardingStep(step);
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

async function loadAccountProfile(page: PreparationExperiencePage): Promise<void> {
  const session = currentAuthSession();
  if (!session) return;
  try {
    const response = await new AccountApi(
      new ApiClient(undefined, session.accessToken),
    ).getOverview();
    const displayName = response.data.displayName?.trim() || "岸岸学员";
    page.setData({
      displayName,
      originalDisplayName: displayName,
      avatarInitial: Array.from(displayName).at(0) ?? "岸",
      avatarStyle: response.data.avatarStyle,
      accountLoading: false,
      profileError: "",
    });
  } catch {
    page.setData({
      accountLoading: false,
      profileError: "微信昵称没有读取成功，请重新读取。",
    });
  }
}

async function saveIdentityAndExperience(page: PreparationExperiencePage): Promise<void> {
  const session = currentAuthSession();
  const controller = runtimes.get(page)?.controller;
  const displayName = page.data.displayName.trim().replace(/\s+/gu, " ");
  if (!session || !controller || page.data.profileSaving || page.data.state?.saving) return;
  if (!displayName || Array.from(displayName).length > 24) {
    page.setData({ profileError: "昵称请填写 1–24 个字。" });
    return;
  }
  page.setData({ profileSaving: true, profileError: "" });
  try {
    if (displayName !== page.data.originalDisplayName) {
      const response = await new AccountApi(
        new ApiClient(undefined, session.accessToken),
      ).updateProfile({
        displayName,
        avatarStyle: page.data.avatarStyle,
      });
      updateAuthDisplayName(response.data.displayName);
      page.setData({
        displayName: response.data.displayName,
        originalDisplayName: response.data.displayName,
        avatarInitial: Array.from(response.data.displayName).at(0) ?? "岸",
        avatarStyle: response.data.avatarStyle,
      });
    }
    page.setData({ profileSaving: false });
    await controller.submit();
  } catch {
    page.setData({
      profileSaving: false,
      profileError: "身份资料没有保存成功，请重试。",
    });
  }
}

function navigateOnboardingStep(step: OnboardingNextStep): void {
  if (step === "preparation_experience") return;
  const target = onboardingNavigationTarget(step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
