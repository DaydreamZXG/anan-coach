import { ApiClient } from "../../api/client";
import { OnboardingApi } from "../../api/onboarding";
import { QualificationApi } from "../../api/qualification";
import { onboardingNavigationTarget, routes } from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { currentAuthSession } from "../../auth/session";
import {
  QualificationController,
  type QualificationControllerState,
} from "../../features/onboarding/qualification-controller";
import type { OnboardingNextStep } from "../../features/onboarding/types";
import {
  emptyQualificationDraft,
  type QualificationDraft,
} from "../../features/qualification/onboarding";

const steps = [
  { label: "基础", title: "先认识真实的你", copy: "学历和专业决定第一层可报范围。" },
  { label: "身份", title: "确认报考身份", copy: "这些是岗位表里的硬条件，不用于评价你。" },
  { label: "经历", title: "补齐经历与户籍", copy: "不确定可以留空，系统会明确标记待确认。" },
  { label: "确认", title: "请核对资格事实", copy: "岗位 Agent 只依据你确认的事实做判断。" },
] as const;

interface QualificationPageData extends QualificationDraft {
  current: number;
  steps: typeof steps;
  error: string;
  requestId: string;
  saving: boolean;
  saved: boolean;
  profileVersion: number;
  loadingProfile: boolean;
  loadFailed: boolean;
  pendingRecovery: boolean;
  primaryActionLabel: string;
}

interface QualificationPageCustom {
  retryLoad(): void;
  chooseEducation(event: WechatMiniprogram.BaseEvent): void;
  inputMajor(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  chooseFreshGraduate(event: WechatMiniprogram.BaseEvent): void;
  choosePoliticalStatus(event: WechatMiniprogram.BaseEvent): void;
  chooseGrassrootsYears(event: WechatMiniprogram.BaseEvent): void;
  inputHukou(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  previous(): void;
  next(): void;
  continueOnboarding(): void;
}

type QualificationPage = WechatMiniprogram.Page.Instance<
  QualificationPageData,
  QualificationPageCustom
>;

const runtimes = new PageControllerRegistry<QualificationPage, QualificationController>();

Page<QualificationPageData, QualificationPageCustom>({
  data: {
    ...emptyQualificationDraft,
    current: 0,
    steps,
    error: "",
    requestId: "",
    saving: false,
    saved: false,
    profileVersion: 0,
    loadingProfile: true,
    loadFailed: false,
    pendingRecovery: false,
    primaryActionLabel: "继续",
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

  retryLoad() {
    void runtimes.get(this)?.controller?.load();
  },

  chooseEducation(event) {
    runtimes
      .get(this)
      ?.controller?.changeDraft({ education: String(event.currentTarget.dataset.value ?? "") });
  },

  inputMajor(event) {
    runtimes.get(this)?.controller?.changeDraft({ major: event.detail.value.trimStart() });
  },

  chooseFreshGraduate(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "true" || value === "false") {
      runtimes.get(this)?.controller?.changeDraft({ freshGraduate: value });
    }
  },

  choosePoliticalStatus(event) {
    runtimes.get(this)?.controller?.changeDraft({
      politicalStatus: String(event.currentTarget.dataset.value ?? ""),
    });
  },

  chooseGrassrootsYears(event) {
    const value = Number(event.currentTarget.dataset.value);
    if (Number.isSafeInteger(value)) {
      runtimes.get(this)?.controller?.changeDraft({ grassrootsYears: value });
    }
  },

  inputHukou(event) {
    runtimes.get(this)?.controller?.changeDraft({ hukou: event.detail.value.trimStart() });
  },

  previous() {
    runtimes.get(this)?.controller?.previous();
  },

  next() {
    runtimes.get(this)?.controller?.next();
  },

  continueOnboarding() {
    runtimes.get(this)?.controller?.continue();
  },
});

function mountController(page: QualificationPage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const client = new ApiClient(undefined, session.accessToken);
  const runtime = runtimes.attach(page);
  const controller = new QualificationController({
    userId: session.userId,
    profileApi: new QualificationApi(client),
    progressApi: new OnboardingApi(client),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) page.setData(pageState(state));
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
  page.setData(pageState(controller.snapshot()));
  void controller.load();
}

function pageState(state: QualificationControllerState): QualificationPageData {
  return {
    ...state.draft,
    current: state.current,
    steps,
    error: state.validationError || state.error?.message || "",
    requestId: state.error?.requestId ?? "",
    saving: state.saving,
    saved: state.saved,
    profileVersion: state.profileVersion,
    loadingProfile: state.status === "idle" || state.status === "loading",
    loadFailed: state.status === "error",
    pendingRecovery: state.pendingRecovery,
    primaryActionLabel: state.pendingRecovery
      ? "查询原确认结果"
      : state.current === 3
        ? "确认并生成可报范围"
        : "继续",
  };
}

function navigateOnboardingStep(step: OnboardingNextStep): void {
  if (step === "qualification") return;
  const target = onboardingNavigationTarget(step);
  if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
  else void wx.redirectTo({ url: target.url });
}
