import { ApiClient } from "../../api/client";
import { DailyOpeningApi } from "../../api/daily-opening";
import type { DailyOpeningView, TodayDashboard } from "../../api/generated/types.gen";
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
  openFuzzyWeatherLocationSetting,
  readFuzzyWeatherLocation,
  WeatherLocationPermissionDeniedError,
} from "../../platform/weather-location";
import {
  DailyOpeningController,
  type DailyOpeningState,
} from "../../features/daily-opening/controller";
import {
  presentDailyOpening,
  type DailyOpeningPresentation,
} from "../../features/daily-opening/presentation";
import {
  TodayController,
  type TodayCapacitySubmission,
  type TodayControllerState,
} from "../../features/planning/today-controller";
import {
  capacityDurationLabel,
  capacityDraftFromVersion,
  capacityScaleLabel,
  capacityTimeOptions,
  capacityWritePolicy,
  createCapacityIdempotencyKey,
  prepareCapacityCommand,
  type CapacityTimeOption,
  type CapacityDraft,
} from "./capacity";
import { presentTodayDashboard, type TodayView } from "./presentation";

class TodayPageControllers {
  constructor(
    readonly today: TodayController,
    readonly opening: DailyOpeningController,
  ) {}

  dispose(): void {
    this.today.dispose();
    this.opening.dispose();
  }
}

const controllers = new PageControllerRegistry<object, TodayPageControllers>();

interface TodayPageData {
  status: "loading" | "ready" | "error";
  errorMessage: string;
  view: TodayView | null;
  displayedPlanVersionId: string | null;
  capacityEditorOpen: boolean;
  capacityDraft: CapacityDraft | null;
  capacityDurationLabel: string;
  capacityTimeOptions: ReadonlyArray<CapacityTimeOption>;
  capacityScaleOptions: number[];
  capacitySubmission: TodayCapacitySubmission;
  capacityMessage: string;
  capacityWriteAllowed: boolean;
  capacityWriteNotice: string;
  energyLabel: string;
  stressLabel: string;
  confidenceLabel: string;
  openingStatus: DailyOpeningState["status"];
  openingView: DailyOpeningPresentation | null;
  openingRaw: DailyOpeningView | null;
  openingError: string;
  openingWriting: boolean;
  openingCollapsed: boolean;
  weatherLocationStatus: "idle" | "requesting" | "denied" | "error";
  weatherLocationMessage: string;
  insightExpanded: boolean;
  taskOpeningId: string;
  taskNavigationError: string;
  headerGreeting: string;
  headerContextLabel: string;
}

interface TodayPageCustom {
  loadDashboard(): Promise<void>;
  replaceDashboard(dashboard: TodayDashboard, capacitySubmission: TodayCapacitySubmission): void;
  toggleCapacityEditor(): void;
  inputAvailableMinutes(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  chooseCapacityMinutes(event: WechatMiniprogram.BaseEvent): void;
  chooseCapacityScale(event: WechatMiniprogram.BaseEvent): void;
  changeCapacityDraft(changes: Partial<CapacityDraft>): void;
  submitCapacity(): Promise<void>;
  reloadAfterCapacityConflict(): void;
  openTask(event: WechatMiniprogram.BaseEvent): void;
  openMasteryQueue(): void;
  openPlanBasis(): void;
  openDailySettlement(): void;
  openWeeklyPlan(): void;
  openCaptureImport(): void;
  openQualificationProfile(): void;
  openStudyPreference(): void;
  openDayAdjustment(): void;
  toggleInsight(): void;
  retry(): void;
  loadDailyOpening(): Promise<void>;
  requestWeatherLocation(): Promise<void>;
  syncWeatherLocation(interactive: boolean): Promise<void>;
  startDailyOpening(): void;
  declineDailyCoffee(): void;
  openCoffeeAssistant(): void;
}

Page<TodayPageData, TodayPageCustom>({
  data: {
    status: "loading",
    errorMessage: "",
    view: null,
    displayedPlanVersionId: null,
    capacityEditorOpen: false,
    capacityDraft: null,
    capacityDurationLabel: "",
    capacityTimeOptions,
    capacityScaleOptions: [1, 2, 3, 4, 5],
    capacitySubmission: "idle",
    capacityMessage: "",
    capacityWriteAllowed: false,
    capacityWriteNotice: "",
    energyLabel: "",
    stressLabel: "",
    confidenceLabel: "",
    openingStatus: "idle",
    openingView: null,
    openingRaw: null,
    openingError: "",
    openingWriting: false,
    openingCollapsed: false,
    weatherLocationStatus: "idle",
    weatherLocationMessage: "",
    insightExpanded: false,
    taskOpeningId: "",
    taskNavigationError: "",
    headerGreeting: greetingLabel(),
    headerContextLabel: "正在读取今日安排",
  },

  onLoad() {
    const runtime = controllers.attach(this);
    const opening = createDailyOpeningController(this);
    const today = createTodayController(this);
    runtime.controller = new TodayPageControllers(today, opening);
    void today.load();
    this.setData({ headerGreeting: greetingLabel(currentAuthSession()?.displayName) });
  },

  onUnload() {
    controllers.detach(this);
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "today" });
    }
    if (this.data.taskOpeningId) this.setData({ taskOpeningId: "" });
    const controller = controllers.get(this)?.controller;
    const latest = consumeLatestTodayDashboard();
    if (latest) controller?.today.acceptLatestDashboard(latest);
    void controller?.today.load();
  },

  async loadDashboard() {
    await controllers.get(this)?.controller?.today.load();
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
    const policy = capacityWritePolicy(currentAccessToken());
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
      capacityDurationLabel: capacityDurationLabel(draft),
      capacitySubmission,
      capacityMessage:
        capacitySubmission === "success" ? "可用时间已确认，今天的安排已重新生成。" : "",
      capacityWriteAllowed: policy.allowed,
      capacityWriteNotice: policy.notice,
      energyLabel: capacityScaleLabel("energy", draft.energy),
      stressLabel: capacityScaleLabel("stress", draft.stress),
      confidenceLabel: capacityScaleLabel("confidence", draft.confidenceLevel),
      headerContextLabel:
        view.positionName === "尚未确认主目标岗位"
          ? "主目标岗位还没有确认"
          : `主目标 · ${view.positionName}`,
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
      capacityDurationLabel: draft ? capacityDurationLabel(draft) : "",
    });
  },

  inputAvailableMinutes(event) {
    this.changeCapacityDraft({ availableMinutes: event.detail.value });
  },

  chooseCapacityMinutes(event) {
    const minutes = Number(event.currentTarget.dataset.minutes);
    if (!Number.isInteger(minutes) || minutes < 0 || minutes > 720) return;
    this.changeCapacityDraft({ availableMinutes: String(minutes) });
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
      capacityDurationLabel: capacityDurationLabel(draft),
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
    const policy = capacityWritePolicy(token);
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

    await controllers.get(this)?.controller?.today.submitCapacity(prepared.command);
  },

  reloadAfterCapacityConflict() {
    if (this.data.capacitySubmission === "submitting") return;
    this.setData({ capacityEditorOpen: true });
    void this.loadDashboard();
  },

  openTask(event: WechatMiniprogram.BaseEvent) {
    const taskId = String(event.currentTarget.dataset.id ?? "");
    if (!taskId || this.data.taskOpeningId) return;
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
    this.setData({ taskOpeningId: task.id, taskNavigationError: "" });
    const fail = (result: WechatMiniprogram.GeneralCallbackResult) => {
      this.setData({
        taskOpeningId: "",
        taskNavigationError: result.errMsg || "任务页面没有打开，请重试。",
      });
    };
    if (task.navigationMode === "switchTab") {
      wx.switchTab({ url: task.navigationUrl, fail });
      return;
    }
    wx.navigateTo({ url: task.navigationUrl, fail });
  },

  openMasteryQueue() {
    void wx.navigateTo({ url: routes.xingceMastery });
  },

  openPlanBasis() {
    void wx.navigateTo({ url: routes.planBasis });
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

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
  },

  openStudyPreference() {
    void wx.navigateTo({ url: routes.studyPreference });
  },

  openDayAdjustment() {
    void wx.navigateTo({ url: routes.dayAdjustment });
  },

  toggleInsight() {
    this.setData({ insightExpanded: !this.data.insightExpanded });
  },

  retry() {
    void this.loadDashboard();
  },

  async loadDailyOpening() {
    await controllers.get(this)?.controller?.opening.load();
  },

  async requestWeatherLocation() {
    await this.syncWeatherLocation(true);
  },

  async syncWeatherLocation(interactive) {
    if (this.data.weatherLocationStatus === "requesting") return;
    if (interactive && this.data.weatherLocationStatus === "denied") {
      const enabled = await openFuzzyWeatherLocationSetting();
      if (!enabled) return;
    }
    this.setData({
      weatherLocationStatus: "requesting",
      weatherLocationMessage: "",
    });
    try {
      const location = await readFuzzyWeatherLocation();
      await controllers.get(this)?.controller?.opening.updateWeatherLocation({
        label: "当前位置附近",
        latitude: location.latitude,
        longitude: location.longitude,
        sourceType: "wechat_fuzzy_location",
      });
      this.setData({
        weatherLocationStatus: "idle",
        weatherLocationMessage: "",
      });
    } catch (error) {
      const denied = error instanceof WeatherLocationPermissionDeniedError;
      this.setData({
        weatherLocationStatus: denied ? "denied" : "error",
        weatherLocationMessage: denied
          ? "开启定位后，岸岸会自动显示附近天气。"
          : error instanceof Error
            ? error.message
            : "暂时无法获取附近天气。",
      });
    }
  },

  startDailyOpening() {
    const startUrl = this.data.openingRaw?.plan.startUrl;
    if (!startUrl) return;
    this.setData({ openingCollapsed: true });
    if (startUrl.split("?")[0] === routes.today) {
      void wx.pageScrollTo({ selector: ".focus-dashboard", duration: 260 });
      return;
    }
    void wx.navigateTo({ url: startUrl });
  },

  declineDailyCoffee() {
    void controllers.get(this)?.controller?.opening.declineCoffee();
  },

  openCoffeeAssistant() {
    void wx.showModal({
      title: "咖啡助手尚未启用",
      content: "只有官方用户授权与下单确认链路验收通过后才会开放；当前不会创建订单或付款。",
      showCancel: false,
    });
  },
});

function greetingLabel(displayName?: string | null): string {
  const hour = new Date().getHours();
  const prefix = hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const name = displayName?.trim();
  return name ? `${prefix}，${name}` : prefix;
}

interface TodayPageInstance {
  data: TodayPageData;
  setData(state: Partial<TodayPageData>): void;
  replaceDashboard(dashboard: TodayDashboard, capacitySubmission: TodayCapacitySubmission): void;
  loadDailyOpening(): Promise<void>;
  syncWeatherLocation(interactive: boolean): Promise<void>;
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
    if (page.data.openingStatus === "idle") void page.loadDailyOpening();
    return;
  }
  page.setData({
    status: state.status === "redirecting" ? "loading" : state.status,
    errorMessage: state.errorMessage,
    view: null,
    displayedPlanVersionId: null,
    capacityEditorOpen: false,
    capacityDraft: null,
    capacityDurationLabel: "",
    capacitySubmission: state.capacitySubmission,
    capacityMessage: state.capacityMessage,
    capacityWriteAllowed: false,
    capacityWriteNotice: "",
    energyLabel: "",
    stressLabel: "",
    confidenceLabel: "",
  });
}

function createDailyOpeningController(page: TodayPageInstance): DailyOpeningController {
  const api = (accessToken: string) =>
    new DailyOpeningApi(new ApiClient(environment.apiBaseUrl, accessToken));
  return new DailyOpeningController({
    accessToken: currentAccessToken,
    get: async (accessToken) => (await api(accessToken).get()).data,
    recordImpression: async (accessToken, command, idempotencyKey) => {
      await api(accessToken).recordImpression(command, idempotencyKey);
    },
    updateWeatherLocation: async (accessToken, command, idempotencyKey) => {
      await api(accessToken).updateWeatherLocation(command, idempotencyKey);
    },
    updatePreference: async (accessToken, command, idempotencyKey) => {
      await api(accessToken).updatePreference(command, idempotencyKey);
    },
    declineCoffee: async (accessToken, idempotencyKey) => {
      await api(accessToken).recordCoffeeDecision({ decision: "declined_today" }, idempotencyKey);
    },
    createIdempotencyKey: createCapacityIdempotencyKey,
    publish: (state) => {
      publishOpeningState(page, state);
    },
  });
}

function publishOpeningState(page: TodayPageInstance, state: DailyOpeningState): void {
  page.setData({
    openingStatus: state.status,
    openingView: state.view ? presentDailyOpening(state.view) : null,
    openingRaw: state.view,
    openingError: state.errorMessage,
    openingWriting: state.writing,
    openingCollapsed: page.data.openingCollapsed || state.view?.displayState === "already_seen",
  });
  if (state.status === "ready" && state.view) {
    wx.nextTick(() => {
      if (
        state.view?.weather.state === "location_required" &&
        page.data.weatherLocationStatus === "idle"
      ) {
        void page.syncWeatherLocation(false);
      }
      void controllers.get(page)?.controller?.opening.recordDisplayed();
    });
  }
}
