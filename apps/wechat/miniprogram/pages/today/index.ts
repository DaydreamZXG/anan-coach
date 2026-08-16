import { ApiClient } from "../../api/client";
import { DailyOpeningApi } from "../../api/daily-opening";
import type {
  DailyOpeningView,
  InferenceCandidateVersion,
  LearnerCoachContext,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { OnboardingApi } from "../../api/onboarding";
import { PlanningApi } from "../../api/planning";
import { createCoachRepository } from "../../data/coach-repository";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import {
  onboardingNavigationTarget,
  questionBookImportUrl,
  registeredNavigationTarget,
  routes,
} from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { clearAuthSession, currentAccessToken, currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { consumeLatestTodayDashboard } from "../../features/planning/runtime-state";
import {
  clearCoachTaskBeforeSnapshot,
  coachLocalDate,
  readCoachTaskBeforeSnapshot,
  storeCoachTaskBeforeSnapshot,
  type CoachTaskBeforeSnapshot,
} from "../../features/coach-loop/feedback-store";
import {
  clearCoachEntryIntent,
  readCoachEntryIntent,
  type CoachEntryIntent,
} from "../../features/onboarding/coach-entry";
import { suppressNativeTabBar } from "../../platform/tab-bar";
import { navigateWithImmediateFeedback } from "../../platform/navigation-feedback";
import { appShareMessage, appTimelineShare, enableAppShareMenu } from "../../platform/share";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  openWeatherLocationSetting,
  readWeatherLocation,
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
import { capacityScaleLabel, createCapacityIdempotencyKey } from "./capacity";
import {
  coachEvidenceCounters,
  coachReturnFeedbackConsumedAuthoritatively,
  presentTodayDashboard,
  type CoachLoopPresentationContext,
  type TodayView,
} from "./presentation";

class TodayPageControllers {
  private initialShowPending = true;

  constructor(
    readonly today: TodayController,
    readonly opening: DailyOpeningController,
  ) {}

  dispose(): void {
    this.today.dispose();
    this.opening.dispose();
  }

  consumeInitialShow(): boolean {
    if (!this.initialShowPending) return false;
    this.initialShowPending = false;
    return true;
  }
}

const controllers = new PageControllerRegistry<object, TodayPageControllers>();

interface TodayCoachRuntime {
  dashboard: TodayDashboard | null;
  profileStatus: CoachLoopPresentationContext["profileStatus"];
  coachContext: LearnerCoachContext | null;
  candidate: InferenceCandidateVersion | null;
  beforeSnapshot: CoachTaskBeforeSnapshot | null;
  entryIntent: CoachEntryIntent | null;
  refreshVersion: number;
  suggestionRequested: boolean;
}

const coachRuntimes = new WeakMap<object, TodayCoachRuntime>();

interface TodayPageData {
  status: "loading" | "ready" | "error";
  errorMessage: string;
  view: TodayView | null;
  capacitySubmission: TodayCapacitySubmission;
  capacityMessage: string;
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
  coachDecisionSubmitting: boolean;
  coachDecisionMessage: string;
  headerGreeting: string;
  headerContextLabel: string;
}

interface TodayPageCustom {
  loadDashboard(): Promise<void>;
  replaceDashboard(dashboard: TodayDashboard, capacitySubmission: TodayCapacitySubmission): void;
  openTask(event: WechatMiniprogram.BaseEvent): void;
  openTrack(event: WechatMiniprogram.BaseEvent): void;
  openMasteryQueue(): void;
  openPlanBasis(): void;
  openDailySettlement(): void;
  openWeeklyPlan(): void;
  openMockExam(): void;
  openCaptureImport(): void;
  openRecognitionOptions(): void;
  openQualificationProfile(): void;
  openStudyPreference(): void;
  openDayAdjustment(): void;
  openQuestionBookImport(): void;
  toggleInsight(): void;
  decideCoachSuggestion(event: WechatMiniprogram.BaseEvent): Promise<void>;
  retry(): void;
  loadDailyOpening(): Promise<void>;
  requestWeatherLocation(): Promise<void>;
  syncWeatherLocation(interactive: boolean): Promise<void>;
  startDailyOpening(): void;
  declineDailyCoffee(): void;
}

Page<TodayPageData, TodayPageCustom>({
  data: {
    status: "loading",
    errorMessage: "",
    view: null,
    capacitySubmission: "idle",
    capacityMessage: "",
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
    coachDecisionSubmitting: false,
    coachDecisionMessage: "",
    headerGreeting: greetingLabel(),
    headerContextLabel: "正在读取今日安排",
  },

  onLoad() {
    const runtime = controllers.attach(this);
    const opening = createDailyOpeningController(this);
    const today = createTodayController(this);
    runtime.controller = new TodayPageControllers(today, opening);
    coachRuntimes.set(this, emptyTodayCoachRuntime());
    void today.load();
    this.setData({ headerGreeting: greetingLabel(currentAuthSession()?.displayName) });
  },

  onUnload() {
    coachRuntimes.delete(this);
    controllers.detach(this);
  },

  onShow() {
    enableAppShareMenu();
    suppressNativeTabBar();
    void wx.pageScrollTo({ scrollTop: 0, duration: 0 });
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "today" });
    }
    if (this.data.taskOpeningId) this.setData({ taskOpeningId: "" });
    const controller = controllers.get(this)?.controller;
    const latest = consumeLatestTodayDashboard();
    if (latest) controller?.today.acceptLatestDashboard(latest);
    if (!controller?.consumeInitialShow()) void controller?.today.load();
  },

  onShareAppMessage() {
    return appShareMessage();
  },

  onShareTimeline() {
    return appTimelineShare();
  },

  async loadDashboard() {
    await controllers.get(this)?.controller?.today.load();
  },

  replaceDashboard(dashboard, capacitySubmission) {
    const coachRuntime = requireTodayCoachRuntime(this);
    coachRuntime.dashboard = dashboard;
    const session = currentAuthSession();
    coachRuntime.beforeSnapshot = session ? readCoachTaskBeforeSnapshot(session.userId) : null;
    coachRuntime.entryIntent = session ? readCoachEntryIntent(session.userId) : null;
    const view = presentTodayDashboard(dashboard, coachPresentationContext(coachRuntime));
    this.setData({
      status: "ready",
      errorMessage: "",
      view,
      capacitySubmission,
      energyLabel: capacityScaleLabel("energy", view.energy),
      stressLabel: capacityScaleLabel("stress", view.stress),
      confidenceLabel: capacityScaleLabel("confidence", view.confidenceLevel),
      headerContextLabel:
        view.positionName === "目标分待生成" ? "国考学习计划" : `学习目标 · ${view.positionName}`,
    });
    void refreshTodayCoachContext(this);
  },

  openTask(event: WechatMiniprogram.BaseEvent) {
    const taskId = String(event.currentTarget.dataset.id ?? "");
    openTodayTask(this, taskId);
  },

  openTrack(event: WechatMiniprogram.BaseEvent) {
    const taskId = String(event.currentTarget.dataset.taskId ?? "");
    const track = String(event.currentTarget.dataset.track ?? "");
    if (taskId) {
      openTodayTask(this, taskId);
      return;
    }
    if (track === "xingce") {
      this.openMasteryQueue();
      return;
    }
    if (track === "essay") this.openDayAdjustment();
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

  openMockExam() {
    void wx.navigateTo({ url: routes.mockExam });
  },

  openCaptureImport() {
    void wx.navigateTo({ url: routes.captureImport });
  },

  openRecognitionOptions() {
    void wx.navigateTo({ url: routes.onboardingEntry });
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

  openQuestionBookImport() {
    void wx.navigateTo({ url: questionBookImportUrl() });
  },

  toggleInsight() {
    this.setData({ insightExpanded: !this.data.insightExpanded });
  },

  async decideCoachSuggestion(event) {
    if (this.data.coachDecisionSubmitting) return;
    const action = String(event.currentTarget.dataset.action);
    if (action !== "confirm" && action !== "reject") return;
    const session = currentAuthSession();
    const runtime = coachRuntimes.get(this);
    const candidate = runtime?.candidate;
    if (!session || !runtime || !candidate || candidate.status !== "awaiting_confirmation") {
      return;
    }
    this.setData({ coachDecisionSubmitting: true, coachDecisionMessage: "" });
    try {
      const { data } = await new LearningBrainApi(
        new ApiClient(environment.apiBaseUrl, session.accessToken),
      ).decideStrategyCandidate(
        candidate.candidateId,
        {
          basedOnCandidateVersionId: candidate.candidateVersionId,
          action,
        },
        await createIdempotencyKey("today-coach-strategy-decision"),
      );
      runtime.candidate = data.candidate;
      this.setData({
        coachDecisionSubmitting: false,
        coachDecisionMessage:
          action === "confirm"
            ? "收到。这个方向现在才会进入后续计划，岸岸会继续复测。"
            : "收到。岸岸不会采用这个判断，当前计划不受影响。",
      });
      renderTodayCoachView(this);
      await controllers.get(this)?.controller?.today.load();
    } catch (error) {
      this.setData({
        coachDecisionSubmitting: false,
        coachDecisionMessage:
          error instanceof Error ? error.message : "这个判断暂时没有提交成功，再试一次。",
      });
    }
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
      const enabled = await openWeatherLocationSetting();
      if (!enabled) return;
    }
    this.setData({
      weatherLocationStatus: "requesting",
      weatherLocationMessage: "",
    });
    try {
      const location = await readWeatherLocation();
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
    const target = registeredNavigationTarget(startUrl);
    if (!target) {
      this.setData({ openingError: "学习入口暂时打不开，稍后再试试。" });
      return;
    }
    this.setData({ openingError: "" });
    if (target.url.split("?")[0] === routes.today) {
      this.setData({ openingCollapsed: true });
      wx.pageScrollTo({
        selector: ".task-heading",
        duration: 260,
        fail: (result) => {
          console.warn("[today] pageScrollTo(.task-heading) failed:", result.errMsg);
        },
      });
      return;
    }
    const success = () => {
      this.setData({ openingCollapsed: true });
    };
    const fail = (result: WechatMiniprogram.GeneralCallbackResult) => {
      console.warn("opening navigation failed", result.errMsg);
      this.setData({ openingError: "页面没打开，别急，再试一次。" });
    };
    if (target.mode === "switchTab") {
      wx.switchTab({ url: target.url, success, fail });
      return;
    }
    wx.navigateTo({ url: target.url, success, fail });
  },

  declineDailyCoffee() {
    void controllers.get(this)?.controller?.opening.declineCoffee();
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

function openTodayTask(page: TodayPageInstance, taskId: string): void {
  if (!taskId || page.data.taskOpeningId) return;
  const task = page.data.view?.tasks.find((item) => item.id === taskId);
  if (!task) return;
  if (!task.canOpen || !task.navigationUrl || !task.navigationMode) {
    void wx.showModal({
      title: "任务暂不可开始",
      content: task.disabledReason,
      showCancel: false,
    });
    return;
  }
  captureCoachTaskBeforeNavigation(page, task);
  page.setData({ taskOpeningId: task.id, taskNavigationError: "" });
  const fail = (result: WechatMiniprogram.GeneralCallbackResult) => {
    console.warn("task navigation failed", result.errMsg);
    page.setData({
      taskOpeningId: "",
      taskNavigationError: "任务页面没打开，再试一次就好。",
    });
    const session = currentAuthSession();
    if (session) clearCoachTaskBeforeSnapshot(session.userId);
  };
  void navigateWithImmediateFeedback(
    { mode: task.navigationMode, url: task.navigationUrl },
    "正在打开任务",
  ).catch((error: unknown) => {
    fail({
      errMsg: error instanceof Error ? error.message : "navigation failed",
    });
  });
}

function emptyTodayCoachRuntime(): TodayCoachRuntime {
  return {
    dashboard: null,
    profileStatus: "loading",
    coachContext: null,
    candidate: null,
    beforeSnapshot: null,
    entryIntent: null,
    refreshVersion: 0,
    suggestionRequested: false,
  };
}

function requireTodayCoachRuntime(page: object): TodayCoachRuntime {
  const current = coachRuntimes.get(page);
  if (current) return current;
  const created = emptyTodayCoachRuntime();
  coachRuntimes.set(page, created);
  return created;
}

function coachPresentationContext(runtime: TodayCoachRuntime): CoachLoopPresentationContext {
  return {
    profileStatus: runtime.profileStatus,
    coachContext: runtime.coachContext,
    candidate: runtime.candidate,
    beforeSnapshot: runtime.beforeSnapshot,
    entryIntent: runtime.entryIntent,
  };
}

async function refreshTodayCoachContext(page: TodayPageInstance): Promise<void> {
  const session = currentAuthSession();
  const runtime = coachRuntimes.get(page);
  if (!session || !runtime?.dashboard) return;
  const refreshVersion = runtime.refreshVersion + 1;
  runtime.refreshVersion = refreshVersion;
  const api = new LearningBrainApi(new ApiClient(environment.apiBaseUrl, session.accessToken));
  try {
    const { data: coachContext } = await api.getCoachContext();
    if (!coachRefreshIsCurrent(page, session.userId, refreshVersion)) return;
    runtime.coachContext = coachContext;
    runtime.profileStatus = "ready";
    if (coachContext.recognition.evidenceCount > 0) {
      clearCoachEntryIntent(session.userId);
      runtime.entryIntent = null;
    }
    renderTodayCoachView(page);
  } catch {
    if (!coachRefreshIsCurrent(page, session.userId, refreshVersion)) return;
    runtime.coachContext = null;
    runtime.profileStatus = "unavailable";
    renderTodayCoachView(page);
  }
  if (runtime.suggestionRequested) return;
  runtime.suggestionRequested = true;
  try {
    const { data } = await api.suggestDailyStrategy();
    if (!coachRefreshIsCurrent(page, session.userId, refreshVersion)) return;
    runtime.candidate = data.status === "suggested" ? (data.candidate ?? null) : null;
    renderTodayCoachView(page);
  } catch {
    if (!coachRefreshIsCurrent(page, session.userId, refreshVersion)) return;
    runtime.candidate = null;
    renderTodayCoachView(page);
  }
}

function coachRefreshIsCurrent(
  page: TodayPageInstance,
  ownerUserId: string,
  refreshVersion: number,
): boolean {
  return (
    currentAuthSession()?.userId === ownerUserId &&
    coachRuntimes.get(page)?.refreshVersion === refreshVersion
  );
}

function renderTodayCoachView(page: TodayPageInstance): void {
  const runtime = coachRuntimes.get(page);
  if (!runtime?.dashboard) return;
  const view = presentTodayDashboard(runtime.dashboard, coachPresentationContext(runtime));
  page.setData({ view });
  const session = currentAuthSession();
  if (
    session &&
    runtime.profileStatus !== "loading" &&
    view.loop?.feedback &&
    coachReturnFeedbackConsumedAuthoritatively(
      runtime.dashboard,
      runtime.coachContext?.profile ?? null,
      runtime.beforeSnapshot,
    )
  ) {
    clearCoachTaskBeforeSnapshot(session.userId);
  }
}

function captureCoachTaskBeforeNavigation(
  page: TodayPageInstance,
  task: TodayView["tasks"][number],
) {
  const session = currentAuthSession();
  const runtime = coachRuntimes.get(page);
  const profile = runtime?.coachContext?.profile;
  if (!session || !runtime?.dashboard) return;
  storeCoachTaskBeforeSnapshot({
    version: 1,
    ownerUserId: session.userId,
    localDate: coachLocalDate(),
    capturedAt: new Date().toISOString(),
    taskId: task.id,
    taskTitle: task.title,
    taskKind: task.kind,
    taskReason: task.reason,
    completionRule: task.completionRule,
    planVersionId: runtime.dashboard.planVersionId,
    ...(profile
      ? {
          profileVersionId: profile.profileVersionId,
          evidence: coachEvidenceCounters(profile),
          errorPatterns: profile.errorPatterns.map((pattern) => ({
            patternId: pattern.patternId,
            displayName: pattern.displayName,
            status: pattern.status,
            counterevidenceCount: pattern.counterevidenceCount,
          })),
        }
      : {}),
  });
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
    const openingPlanChanged =
      page.data.openingRaw !== null &&
      page.data.openingRaw.planVersionId !== state.dashboard.planVersionId;
    page.replaceDashboard(state.dashboard, state.capacitySubmission);
    page.setData({
      status: "ready",
      errorMessage: state.errorMessage,
      capacitySubmission: state.capacitySubmission,
      capacityMessage: state.capacityMessage,
    });
    if (page.data.openingStatus === "idle" || openingPlanChanged) void page.loadDailyOpening();
    return;
  }
  page.setData({
    status: state.status === "redirecting" ? "loading" : state.status,
    errorMessage: state.errorMessage,
    view: null,
    capacitySubmission: state.capacitySubmission,
    capacityMessage: state.capacityMessage,
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
      void controllers.get(page)?.controller?.opening.recordDisplayed();
    });
  }
}
