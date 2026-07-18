import { ApiClient } from "../../api/client";
import type { TodayDashboard } from "../../api/generated/types.gen";
import { OnboardingApi } from "../../api/onboarding";
import { PlanningApi } from "../../api/planning";
import { createCoachRepository } from "../../data/coach-repository";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { onboardingNavigationTarget, routes } from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { clearAuthSession, currentAccessToken, currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { consumeLatestTodayDashboard } from "../../features/planning/runtime-state";
import {
  TodayController,
  type TodayCapacitySubmission,
  type TodayControllerState,
} from "../../features/planning/today-controller";
import {
  capacityDraftFromVersion,
  capacityScaleLabel,
  capacityWritePolicy,
  createCapacityIdempotencyKey,
  prepareCapacityCommand,
  type CapacityDraft,
} from "./capacity";
import { presentTodayDashboard, type TodayView } from "./presentation";

const controllers = new PageControllerRegistry<object, TodayController>();

interface TodayPageData {
  status: "loading" | "ready" | "error";
  errorMessage: string;
  view: TodayView | null;
  displayedPlanVersionId: string | null;
  capacityEditorOpen: boolean;
  capacityDraft: CapacityDraft | null;
  capacityScaleOptions: number[];
  capacitySubmission: TodayCapacitySubmission;
  capacityMessage: string;
  capacityWriteAllowed: boolean;
  capacityWriteNotice: string;
  energyLabel: string;
  stressLabel: string;
  confidenceLabel: string;
}

interface TodayPageCustom {
  loadDashboard(): Promise<void>;
  replaceDashboard(dashboard: TodayDashboard, capacitySubmission: TodayCapacitySubmission): void;
  toggleCapacityEditor(): void;
  inputAvailableMinutes(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  chooseCapacityScale(event: WechatMiniprogram.BaseEvent): void;
  changeCapacityDraft(changes: Partial<CapacityDraft>): void;
  submitCapacity(): Promise<void>;
  reloadAfterCapacityConflict(): void;
  openTask(event: WechatMiniprogram.BaseEvent): void;
  openMasteryQueue(): void;
  openPlanBasis(): void;
  openDayAdjustment(): void;
  openDailySettlement(): void;
  openWeeklyPlan(): void;
  openCaptureImport(): void;
  openOnboarding(): void;
  retry(): void;
}

Page<TodayPageData, TodayPageCustom>({
  data: {
    status: "loading",
    errorMessage: "",
    view: null,
    displayedPlanVersionId: null,
    capacityEditorOpen: false,
    capacityDraft: null,
    capacityScaleOptions: [1, 2, 3, 4, 5],
    capacitySubmission: "idle",
    capacityMessage: "",
    capacityWriteAllowed: false,
    capacityWriteNotice: "",
    energyLabel: "",
    stressLabel: "",
    confidenceLabel: "",
  },

  onLoad() {
    const runtime = controllers.attach(this);
    runtime.controller = createTodayController(this);
    void runtime.controller.load();
  },

  onUnload() {
    controllers.detach(this);
  },

  onShow() {
    const controller = controllers.get(this)?.controller;
    const latest = consumeLatestTodayDashboard();
    if (latest) controller?.acceptLatestDashboard(latest);
    void controller?.load();
  },

  async loadDashboard() {
    await controllers.get(this)?.controller?.load();
  },

  replaceDashboard(dashboard, capacitySubmission) {
    const view = presentTodayDashboard(dashboard);
    const dashboardVersionChanged =
      !this.data.view ||
      this.data.displayedPlanVersionId !== dashboard.planVersionId ||
      this.data.view.capacityVersionId !== view.capacityVersionId;
    const shouldAcceptServerCapacity =
      dashboardVersionChanged || capacitySubmission === "success" || !this.data.capacityDraft;
    const draft = shouldAcceptServerCapacity
      ? capacityDraftFromVersion(view)
      : (this.data.capacityDraft ?? capacityDraftFromVersion(view));
    const policy = capacityWritePolicy(environment.dataMode, currentAccessToken());
    this.setData({
      status: "ready",
      errorMessage: "",
      view,
      displayedPlanVersionId: dashboard.planVersionId,
      capacityEditorOpen:
        capacitySubmission === "success"
          ? false
          : capacitySubmission === "conflict"
            ? true
            : this.data.capacityEditorOpen,
      capacityDraft: draft,
      capacitySubmission,
      capacityMessage:
        capacitySubmission === "success" ? "容量版本已确认，今日计划已按新状态完整重排。" : "",
      capacityWriteAllowed: policy.allowed,
      capacityWriteNotice: policy.notice,
      energyLabel: capacityScaleLabel("energy", draft.energy),
      stressLabel: capacityScaleLabel("stress", draft.stress),
      confidenceLabel: capacityScaleLabel("confidence", draft.confidenceLevel),
    });
  },

  toggleCapacityEditor() {
    if (
      this.data.capacitySubmission === "submitting" ||
      this.data.capacitySubmission === "conflict" ||
      !this.data.view
    ) {
      return;
    }
    const opening = !this.data.capacityEditorOpen;
    const draft = opening ? capacityDraftFromVersion(this.data.view) : this.data.capacityDraft;
    this.setData({
      capacityEditorOpen: opening,
      capacityDraft: draft,
      capacitySubmission: opening ? "idle" : this.data.capacitySubmission,
      capacityMessage: "",
      energyLabel: draft ? capacityScaleLabel("energy", draft.energy) : "",
      stressLabel: draft ? capacityScaleLabel("stress", draft.stress) : "",
      confidenceLabel: draft ? capacityScaleLabel("confidence", draft.confidenceLevel) : "",
    });
  },

  inputAvailableMinutes(event) {
    this.changeCapacityDraft({ availableMinutes: event.detail.value });
  },

  chooseCapacityScale(event) {
    const field = String(event.currentTarget.dataset.field ?? "");
    const value = Number(event.currentTarget.dataset.value);
    if (!Number.isInteger(value) || value < 1 || value > 5) return;
    if (field === "energy" || field === "stress" || field === "confidenceLevel") {
      this.changeCapacityDraft({ [field]: value });
    }
  },

  changeCapacityDraft(changes) {
    const current = this.data.capacityDraft;
    if (!current || this.data.capacitySubmission === "submitting") return;
    const draft = { ...current, ...changes };
    this.setData({
      capacityDraft: draft,
      capacitySubmission: this.data.capacitySubmission === "conflict" ? "conflict" : "idle",
      capacityMessage: this.data.capacitySubmission === "conflict" ? this.data.capacityMessage : "",
      energyLabel: capacityScaleLabel("energy", draft.energy),
      stressLabel: capacityScaleLabel("stress", draft.stress),
      confidenceLabel: capacityScaleLabel("confidence", draft.confidenceLevel),
    });
  },

  async submitCapacity() {
    if (this.data.capacitySubmission === "submitting") return;
    const view = this.data.view;
    const draft = this.data.capacityDraft;
    if (!view || !draft) return;

    const token = currentAccessToken();
    const policy = capacityWritePolicy(environment.dataMode, token);
    if (!policy.allowed) {
      this.setData({
        capacitySubmission: "error",
        capacityMessage: policy.notice,
        capacityWriteAllowed: false,
        capacityWriteNotice: policy.notice,
      });
      return;
    }

    const prepared = prepareCapacityCommand(draft, view.capacityVersionId);
    if (!prepared.command) {
      this.setData({ capacitySubmission: "error", capacityMessage: prepared.error });
      return;
    }

    await controllers.get(this)?.controller?.submitCapacity(prepared.command);
  },

  reloadAfterCapacityConflict() {
    if (this.data.capacitySubmission === "submitting") return;
    this.setData({ capacityEditorOpen: true });
    void this.loadDashboard();
  },

  openTask(event: WechatMiniprogram.BaseEvent) {
    const taskId = String(event.currentTarget.dataset.id ?? "");
    const task = this.data.view?.tasks.find((item) => item.id === taskId);
    if (!task) return;
    if (!task.canOpen || !task.navigationUrl || !task.navigationMode) {
      void wx.showModal({
        title: "任务暂不可开始",
        content: task.disabledReason,
        showCancel: false,
      });
      return;
    }
    if (task.navigationMode === "switchTab") {
      void wx.switchTab({ url: task.navigationUrl });
      return;
    }
    void wx.navigateTo({ url: task.navigationUrl });
  },

  openMasteryQueue() {
    void wx.navigateTo({ url: routes.xingceMastery });
  },

  openPlanBasis() {
    void wx.navigateTo({ url: routes.planBasis });
  },

  openDayAdjustment() {
    void wx.navigateTo({ url: routes.dayAdjustment });
  },

  openDailySettlement() {
    void wx.navigateTo({ url: routes.dailySettlement });
  },

  openWeeklyPlan() {
    void wx.navigateTo({ url: routes.weeklyPlan });
  },

  openCaptureImport() {
    void wx.navigateTo({ url: routes.captureImport });
  },

  openOnboarding() {
    void wx.navigateTo({ url: routes.onboarding });
  },

  retry() {
    void this.loadDashboard();
  },
});

interface TodayPageInstance {
  data: TodayPageData;
  setData(state: Partial<TodayPageData>): void;
  replaceDashboard(dashboard: TodayDashboard, capacitySubmission: TodayCapacitySubmission): void;
}

function createTodayController(page: TodayPageInstance): TodayController {
  return new TodayController({
    currentSession: currentAuthSession,
    getProgress: async (accessToken) =>
      (await new OnboardingApi(new ApiClient(environment.apiBaseUrl, accessToken)).getProgress())
        .data,
    getToday: async (accessToken) => createCoachRepository(accessToken).getToday(),
    setCapacity: async (accessToken, command, idempotencyKey) =>
      (
        await new PlanningApi(new ApiClient(environment.apiBaseUrl, accessToken)).setTodayCapacity(
          command,
          idempotencyKey,
        )
      ).data,
    createIdempotencyKey: createCapacityIdempotencyKey,
    clearSession: clearAuthSession,
    reauthenticate: async () => {
      await wx.reLaunch({ url: loginUrlForReturn(routes.today) });
    },
    navigateOnboarding: async (nextStep) => {
      await wx.reLaunch({ url: onboardingNavigationTarget(nextStep).url });
    },
    publish: (state) => {
      publishTodayState(page, state);
    },
  });
}

function publishTodayState(page: TodayPageInstance, state: TodayControllerState): void {
  if (state.dashboard) {
    const previousConflictMessage =
      page.data.capacitySubmission === "conflict" ? page.data.capacityMessage : "";
    page.replaceDashboard(state.dashboard, state.capacitySubmission);
    page.setData({
      status: "ready",
      errorMessage: state.errorMessage,
      capacitySubmission: state.capacitySubmission,
      capacityMessage:
        state.capacitySubmission === "conflict" &&
        previousConflictMessage &&
        !state.capacityMessage.includes("冲突")
          ? `${previousConflictMessage} ${state.capacityMessage}`
          : state.capacityMessage,
    });
    return;
  }
  page.setData({
    status: state.status === "redirecting" ? "loading" : state.status,
    errorMessage: state.errorMessage,
    view: null,
    displayedPlanVersionId: null,
    capacityEditorOpen: false,
    capacityDraft: null,
    capacitySubmission: state.capacitySubmission,
    capacityMessage: state.capacityMessage,
    capacityWriteAllowed: false,
    capacityWriteNotice: "",
    energyLabel: "",
    stressLabel: "",
    confidenceLabel: "",
  });
}
