import { ApiClient } from "../../api/client";
import { OnboardingApi } from "../../api/onboarding";
import { onboardingNavigationTarget, routes } from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { currentAuthSession } from "../../auth/session";
import {
  StudyPreferenceController,
  type StudyPreferenceControllerState,
} from "../../features/onboarding/study-preference-controller";
import {
  studyTimeOptions,
  type StudyMinutesField,
  type StudyTimeOption,
  type Weekday,
} from "../../features/onboarding/study-preference";
import type { OnboardingNextStep } from "../../features/onboarding/types";
import { studyMinutesScrollTargets, type StudyMinutesScrollTargets } from "./scroll-position";

interface StudyPreferencePageData extends StudyMinutesScrollTargets {
  state: StudyPreferenceControllerState | null;
  weekdays: ReadonlyArray<{ value: Weekday; label: string }>;
  timeOptions: ReadonlyArray<StudyTimeOption>;
}

interface StudyPreferencePageCustom {
  adjustMinutes(event: WechatMiniprogram.BaseEvent): void;
  selectMinutes(event: WechatMiniprogram.BaseEvent): void;
  selectDay(event: WechatMiniprogram.BaseEvent): void;
  submit(): void;
  retry(): void;
}

type StudyPreferencePage = WechatMiniprogram.Page.Instance<
  StudyPreferencePageData,
  StudyPreferencePageCustom
>;

const runtimes = new PageControllerRegistry<StudyPreferencePage, StudyPreferenceController>();
const weekdays: StudyPreferencePageData["weekdays"] = [
  { value: "monday", label: "一" },
  { value: "tuesday", label: "二" },
  { value: "wednesday", label: "三" },
  { value: "thursday", label: "四" },
  { value: "friday", label: "五" },
  { value: "saturday", label: "六" },
  { value: "sunday", label: "日" },
];

Page<StudyPreferencePageData, StudyPreferencePageCustom>({
  data: {
    state: null,
    weekdays,
    timeOptions: studyTimeOptions,
    ...studyMinutesScrollTargets({
      weekdayMinutes: studyTimeOptions[0]?.minutes ?? 30,
      weekendMinutes: studyTimeOptions[0]?.minutes ?? 30,
    }),
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
    if ((field === "weekdayMinutes" || field === "weekendMinutes") && Number.isInteger(delta)) {
      runtimes.get(this)?.controller?.adjustMinutes(field, delta);
    }
  },

  selectMinutes(event) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const minutes = Number(event.currentTarget.dataset.minutes);
    if (isStudyMinutesField(field) && Number.isSafeInteger(minutes)) {
      runtimes.get(this)?.controller?.setMinutes(field, minutes);
    }
  },

  selectDay(event) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const value = String(event.currentTarget.dataset.value ?? "");
    if (
      (field === "fixedRestDay" || field === "mockExamDay") &&
      weekdays.some((item) => item.value === value)
    ) {
      runtimes.get(this)?.controller?.selectDay(field, value as Weekday);
    }
  },

  submit() {
    void runtimes.get(this)?.controller?.submit();
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
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
    mode: "settings",
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) {
        page.setData({ state, ...studyMinutesScrollTargets(state.draft) });
      }
    },
    onAdvance: (step) => {
      if (runtimes.owns(page, runtime)) navigateOnboardingStep(step);
    },
    onSaved: () => {
      if (!runtimes.owns(page, runtime)) return;
      void wx.showToast({ title: "学习时间已更新", icon: "success" });
      setTimeout(() => {
        if (!runtimes.owns(page, runtime)) return;
        wx.navigateBack({
          fail: () => {
            void wx.switchTab({ url: routes.me });
          },
        });
      }, 700);
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  const state = controller.snapshot();
  page.setData({ state, ...studyMinutesScrollTargets(state.draft) });
  void controller.load();
}

function isStudyMinutesField(field: string): field is StudyMinutesField {
  return field === "weekdayMinutes" || field === "weekendMinutes";
}

function navigateOnboardingStep(step: OnboardingNextStep): void {
  if (step === "study_preference") return;
  const target = onboardingNavigationTarget(step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
