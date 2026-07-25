import { ApiClient } from "../../../api/client";
import { OnboardingApi } from "../../../api/onboarding";
import { onboardingNavigationTarget, routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  StudyPreferenceController,
  type StudyPreferenceControllerState,
} from "../../../features/onboarding/study-preference-controller";
import type { StudyMinutesField } from "../../../features/onboarding/study-preference";
import type { OnboardingNextStep } from "../../../features/onboarding/types";

interface StudyPreferencePageData {
  state: StudyPreferenceControllerState | null;
  weekdayHoursLabel: string;
  weekendHoursLabel: string;
  exitAfterSave: boolean;
}

interface StudyPreferencePageCustom {
  adjustMinutes(event: WechatMiniprogram.BaseEvent): void;
  changeMinutes(event: WechatMiniprogram.SliderChange): void;
  applyPreset(event: WechatMiniprogram.BaseEvent): void;
  submit(): void;
  saveAndExit(): void;
  retry(): void;
  back(): void;
}

type StudyPreferencePage = WechatMiniprogram.Page.Instance<
  StudyPreferencePageData,
  StudyPreferencePageCustom
>;

const runtimes = new PageControllerRegistry<StudyPreferencePage, StudyPreferenceController>();

Page<StudyPreferencePageData, StudyPreferencePageCustom>({
  data: {
    state: null,
    weekdayHoursLabel: "2.5",
    weekendHoursLabel: "6.0",
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

  adjustMinutes(event) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const delta = Number(event.currentTarget.dataset.delta);
    if (isStudyMinutesField(field) && Number.isInteger(delta)) {
      runtimes.get(this)?.controller?.adjustMinutes(field, delta);
    }
  },

  changeMinutes(event) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const minutes = event.detail.value;
    if (isStudyMinutesField(field) && Number.isSafeInteger(minutes)) {
      runtimes.get(this)?.controller?.setMinutes(field, minutes);
    }
  },

  applyPreset(event) {
    const weekdayMinutes = Number(event.currentTarget.dataset.weekday);
    const weekendMinutes = Number(event.currentTarget.dataset.weekend);
    const controller = runtimes.get(this)?.controller;
    if (
      !controller ||
      !Number.isSafeInteger(weekdayMinutes) ||
      !Number.isSafeInteger(weekendMinutes)
    ) {
      return;
    }
    controller.setMinutes("weekdayMinutes", weekdayMinutes);
    controller.setMinutes("weekendMinutes", weekendMinutes);
  },

  submit() {
    this.setData({ exitAfterSave: false });
    void runtimes.get(this)?.controller?.submit();
  },

  saveAndExit() {
    this.setData({ exitAfterSave: true });
    void runtimes.get(this)?.controller?.submit();
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },

  back() {
    void wx.redirectTo({ url: routes.onboardingPositionPreference });
  },
});

function mountController(page: StudyPreferencePage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const runtime = runtimes.attach(page);
  const controller = new StudyPreferenceController({
    userId: session.userId,
    api: new OnboardingApi(new ApiClient(undefined, session.accessToken)),
    onChange: (state) => {
      if (!runtimes.owns(page, runtime)) return;
      page.setData({
        state,
        weekdayHoursLabel: hoursLabel(state.draft.weekdayMinutes),
        weekendHoursLabel: hoursLabel(state.draft.weekendMinutes),
      });
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
  const state = controller.snapshot();
  page.setData({
    state,
    weekdayHoursLabel: hoursLabel(state.draft.weekdayMinutes),
    weekendHoursLabel: hoursLabel(state.draft.weekendMinutes),
  });
  void controller.load();
}

function isStudyMinutesField(field: string): field is StudyMinutesField {
  return field === "weekdayMinutes" || field === "weekendMinutes";
}

function hoursLabel(minutes: number): string {
  return (minutes / 60).toFixed(1);
}

function navigateOnboardingStep(step: OnboardingNextStep): void {
  if (step === "study_preference") return;
  const target = onboardingNavigationTarget(step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
