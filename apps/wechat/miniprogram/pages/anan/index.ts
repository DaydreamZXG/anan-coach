import { CompanionApi } from "../../api/companion";
import type {
  CompanionAskRequest,
  CompanionAskResponse,
  LearningBrainProfileVersion,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { OnboardingApi } from "../../api/onboarding";
import { PlanningApi } from "../../api/planning";
import { routes } from "../../app/navigation/routes";
import {
  companionAnswerView,
  companionContextView,
  companionFailureView,
  type CompanionAnswerView,
  type CompanionContextSourceState,
} from "../../features/companion/presentation";
import { resolveCompanionAction } from "../../features/learning-brain/companion-action";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { ApiClientError, type ApiClient } from "../../api/client";
import { stableFingerprint } from "../../platform/command-id";
import { suppressNativeTabBar } from "../../platform/tab-bar";
import { OperationAttemptStore } from "../../platform/operation-attempt-store";
import {
  consumeCompanionLaunchContext,
  type CompanionLaunchContext,
} from "../../features/companion/context-store";
import { currentAuthSession, sameSessionOwner, type ActiveAuthSession } from "../../auth/session";

const stateLabels: Record<CompanionAskResponse["semanticState"], string> = {
  ready: "已经根据手头的信息回答啦",
  evidence_insufficient: "手头信息还不够",
  permission_denied: "这个页面暂时答不了",
  security_blocked: "这个问题不太对劲,先不回答",
  quota_exhausted: "今天的提问次数用完啦,明天再来",
  provider_pending: "回答还在路上,稍等一下",
  failed: "这次没回答成,别急",
};

const companionAskScope = "companion-ask";
const companionAttemptTtlMs = 7 * 24 * 60 * 60 * 1000;
const companionPendingMessage = "哎,刚才的回答还没到,别急,再发一次就好。";

interface CompanionContextRuntime {
  ownerSession: ActiveAuthSession | undefined;
  profileSource: CompanionContextSourceState<LearningBrainProfileVersion>;
  todaySource: CompanionContextSourceState<TodayDashboard>;
  profileGeneration: number;
  todayGeneration: number;
  profileRetryInFlight: boolean;
  todayRetryInFlight: boolean;
}

const contextRuntimeByPage = new WeakMap<object, CompanionContextRuntime>();

interface CompanionAskDraft {
  question: string;
  askRouteId: string;
  askWorkspace: CompanionAskRequest["context"]["workspace"];
  askObjectRefs: NonNullable<CompanionAskRequest["context"]["objectRefs"]>;
  askTaskMode: CompanionAskRequest["context"]["taskMode"] | undefined;
  askTaskStage: CompanionAskRequest["context"]["taskStage"] | undefined;
}

function defaultCompanionWorkspace(): CompanionLaunchContext["workspace"] {
  return "training";
}

function loadingContextSource<T>(): CompanionContextSourceState<T> {
  return { status: "loading" };
}

function createContextRuntime(ownerSession?: ActiveAuthSession): CompanionContextRuntime {
  return {
    ownerSession,
    profileSource: loadingContextSource<LearningBrainProfileVersion>(),
    todaySource: loadingContextSource<TodayDashboard>(),
    profileGeneration: 0,
    todayGeneration: 0,
    profileRetryInFlight: false,
    todayRetryInFlight: false,
  };
}

function contextRuntimeFor(page: object): CompanionContextRuntime {
  const existing = contextRuntimeByPage.get(page);
  if (existing) return existing;
  const created = createContextRuntime();
  contextRuntimeByPage.set(page, created);
  return created;
}

function contextRuntimeForSession(
  page: object,
  session: ActiveAuthSession,
): { runtime: CompanionContextRuntime; reset: boolean } {
  const existing = contextRuntimeByPage.get(page);
  if (existing && sameSessionOwner(existing.ownerSession, session)) {
    return { runtime: existing, reset: false };
  }
  if (existing) {
    existing.profileGeneration += 1;
    existing.todayGeneration += 1;
  }
  const runtime = createContextRuntime(session);
  contextRuntimeByPage.set(page, runtime);
  return { runtime, reset: true };
}

Page({
  data: {
    contextStatus: "loading",
    askAvailable: false,
    profileAskAvailable: false,
    recoveryAskAvailable: false,
    profileSourceStatus: "loading",
    todaySourceStatus: "loading",
    profileRefreshing: false,
    todayRefreshing: false,
    manualKeyboard: false,
    keyboardOffset: 0,
    learnerStateVersion: "",
    profileLearnerStateVersion: "",
    question: "",
    askedQuestion: "",
    submitting: false,
    errorMessage: "",
    requestId: "",
    semanticState: "ready",
    stateLabel: "正在看看你的学习情况",
    companionIntent: "companion",
    answer: null as CompanionAnswerView | null,
    contextCoreCopy: "正在把今天的学习情况给你整理好。",
    contextGoalLabel: "正在整理",
    contextTaskLabel: "正在整理",
    contextReviewLabel: "正在整理",
    contextCapacityLabel: "正在整理",
    contextEvidenceLabel: "0 条学习记录",
    contextNoticeVisible: false,
    contextNoticeTitle: "",
    contextNoticeCopy: "",
    profileIssueVisible: false,
    profileSourceLabel: "学习档案 · 正在整理",
    profileSourceMessage: "",
    profileSourceRequestId: "",
    todayIssueVisible: false,
    todaySourceLabel: "今日计划 · 正在整理",
    todaySourceMessage: "",
    todaySourceRequestId: "",
    retryProfileVisible: false,
    retryTodayVisible: false,
    insightVisible: false,
    insightTitle: "正在看看你的备考状态",
    insightCopy: "你的岗位、现在的水平和今天的安排,岸岸都帮你串在一起看。",
    insightActionLabel: "查看今天",
    insightAction: "today",
    insightDismissed: false,
    contextBannerVisible: false,
    contextSourceLabel: "",
    contextTitle: "",
    contextSummary: "",
    askRouteId: "pages/anan/index",
    askWorkspace: defaultCompanionWorkspace(),
    askObjectRefs: [] as CompanionLaunchContext["objectRefs"],
    askTaskMode: undefined as CompanionLaunchContext["taskMode"],
    askTaskStage: undefined as CompanionLaunchContext["taskStage"],
  },

  onLoad(options: Record<string, string | undefined>) {
    const question = options.question?.trim() ?? "";
    const windowWidth = wx.getWindowInfo().windowWidth;
    this.setData({ question: question.slice(0, 800), manualKeyboard: windowWidth >= 768 });
    void this.loadContext();
  },

  onShow() {
    suppressNativeTabBar();
    if (!currentAuthSession()) {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "anan" });
    }
    if (!this.restoreUnfinishedCompanionAttempt()) {
      this.applyLaunchContext();
      this.syncAskAvailability();
    }
  },

  onUnload() {
    const runtime = contextRuntimeByPage.get(this);
    if (!runtime) return;
    runtime.profileGeneration += 1;
    runtime.todayGeneration += 1;
    contextRuntimeByPage.delete(this);
  },

  restoreUnfinishedCompanionAttempt(): boolean {
    const auth = currentAuthSession();
    if (!auth) return false;
    const attempt = new OperationAttemptStore(auth.userId).load(companionAskScope);
    if (
      !attempt ||
      (attempt.state !== "pending" && attempt.state !== "unknown") ||
      !isCompanionAskRequest(attempt.canonicalInput)
    ) {
      return false;
    }
    if (this.data.submitting) return true;
    const command = attempt.canonicalInput;
    this.setData({
      contextStatus: "ready",
      askAvailable: true,
      recoveryAskAvailable: true,
      question: command.questionText,
      askedQuestion: "",
      submitting: false,
      errorMessage: companionPendingMessage,
      requestId: "",
      semanticState: "provider_pending",
      stateLabel: stateLabels.provider_pending,
      companionIntent: "reminder",
      answer: null,
      contextBannerVisible: false,
      contextSourceLabel: "",
      contextTitle: "",
      contextSummary: "",
      askRouteId: command.context.routeId,
      askWorkspace: command.context.workspace,
      askObjectRefs: (command.context.objectRefs ?? []).map((item) => ({ ...item })),
      askTaskMode: command.context.taskMode ?? undefined,
      askTaskStage: command.context.taskStage ?? undefined,
      learnerStateVersion: command.context.learnerStateVersion,
    });
    return true;
  },

  applyLaunchContext() {
    const auth = currentAuthSession();
    if (!auth) return;
    const context = consumeCompanionLaunchContext(auth.userId);
    if (!context) return;
    this.setData({
      contextBannerVisible: true,
      contextSourceLabel: context.sourceLabel,
      contextTitle: context.title,
      contextSummary: context.summary,
      question: context.suggestedQuestion,
      answer: null,
      errorMessage: "",
      askRouteId: context.routeId,
      askWorkspace: context.workspace,
      askObjectRefs: context.objectRefs,
      askTaskMode: context.taskMode,
      askTaskStage: context.taskStage,
    });
  },

  clearLaunchContext() {
    this.setData({
      contextBannerVisible: false,
      contextSourceLabel: "",
      contextTitle: "",
      contextSummary: "",
      askRouteId: "pages/anan/index",
      askWorkspace: "training",
      askObjectRefs: [],
      askTaskMode: undefined,
      askTaskStage: undefined,
    });
    this.syncAskAvailability();
  },

  async loadContext() {
    const auth = currentAuthSession();
    const client = requireAuthenticatedClient();
    if (!auth || !client) return;
    const { runtime, reset } = contextRuntimeForSession(this, auth);
    if (reset) {
      this.applyContextProjection({ profileRefreshing: false, todayRefreshing: false });
    }
    const previousProfile = runtime.profileSource;
    const previousToday = runtime.todaySource;
    const profileGeneration = ++runtime.profileGeneration;
    const todayGeneration = ++runtime.todayGeneration;
    if (!contextSourceHasValue(previousProfile) && !contextSourceHasValue(previousToday)) {
      this.setData({ contextStatus: "loading" });
    }
    this.setData({
      profileRefreshing: true,
      todayRefreshing: true,
      errorMessage: "",
      requestId: "",
    });
    const [profileResult, todayResult] = await Promise.allSettled([
      new LearningBrainApi(client).getProfile().then(({ data }) => data),
      loadTodayDashboardIfReady(client),
    ]);
    if (!sameSessionOwner(currentAuthSession(), auth)) {
      this.recoverExpiredSession();
      return;
    }
    const refreshingPatch: {
      profileRefreshing?: boolean;
      todayRefreshing?: boolean;
    } = {};
    let acceptedResult = false;
    if (runtime.profileGeneration === profileGeneration) {
      runtime.profileSource = settledContextSource(
        profileResult,
        previousProfile,
        "学习档案没读出来,先别急,点下面再读一次。",
      );
      refreshingPatch.profileRefreshing = false;
      acceptedResult = true;
    }
    if (runtime.todayGeneration === todayGeneration) {
      runtime.todaySource = settledTodayContextSource(
        todayResult,
        previousToday,
        "今日计划没读出来,先别急,再点一次。",
      );
      refreshingPatch.todayRefreshing = false;
      acceptedResult = true;
    }
    if (acceptedResult) this.applyContextProjection(refreshingPatch);
  },

  async retryProfileContext() {
    const auth = currentAuthSession();
    const client = requireAuthenticatedClient();
    if (!auth || !client) return;
    const { runtime, reset } = contextRuntimeForSession(this, auth);
    if (reset) {
      this.applyContextProjection({ profileRefreshing: false, todayRefreshing: false });
    }
    if (runtime.profileRetryInFlight || this.data.profileRefreshing) return;
    runtime.profileRetryInFlight = true;
    const previousProfile = runtime.profileSource;
    const profileGeneration = ++runtime.profileGeneration;
    this.setData({ profileRefreshing: true });
    const [profileResult] = await Promise.allSettled([
      new LearningBrainApi(client).getProfile().then(({ data }) => data),
    ]);
    runtime.profileRetryInFlight = false;
    if (!sameSessionOwner(currentAuthSession(), auth)) {
      this.recoverExpiredSession();
      return;
    }
    if (runtime.profileGeneration !== profileGeneration) return;
    runtime.profileSource = settledContextSource(
      profileResult,
      previousProfile,
      "学习档案没读出来,先别急,点下面再读一次。",
    );
    this.applyContextProjection({ profileRefreshing: false });
  },

  async retryTodayContext() {
    const auth = currentAuthSession();
    const client = requireAuthenticatedClient();
    if (!auth || !client) return;
    const { runtime, reset } = contextRuntimeForSession(this, auth);
    if (reset) {
      this.applyContextProjection({ profileRefreshing: false, todayRefreshing: false });
    }
    if (runtime.todayRetryInFlight || this.data.todayRefreshing) return;
    runtime.todayRetryInFlight = true;
    const previousToday = runtime.todaySource;
    const todayGeneration = ++runtime.todayGeneration;
    this.setData({ todayRefreshing: true });
    const [todayResult] = await Promise.allSettled([loadTodayDashboardIfReady(client)]);
    runtime.todayRetryInFlight = false;
    if (!sameSessionOwner(currentAuthSession(), auth)) {
      this.recoverExpiredSession();
      return;
    }
    if (runtime.todayGeneration !== todayGeneration) return;
    runtime.todaySource = settledTodayContextSource(
      todayResult,
      previousToday,
      "今日计划没读出来,先别急,再点一次。",
    );
    this.applyContextProjection({ todayRefreshing: false });
  },

  applyContextProjection(refreshingPatch: {
    profileRefreshing?: boolean;
    todayRefreshing?: boolean;
  }) {
    const runtime = contextRuntimeFor(this);
    const projection = companionContextView(runtime.profileSource, runtime.todaySource);
    const auth = currentAuthSession();
    const recoveryCommand = auth ? recoverableCompanionCommand(auth.userId, this.data) : null;
    const recoveryAskAvailable = recoveryCommand !== null;
    const askAvailable = projection.askAvailable || recoveryAskAvailable;
    const learnerStateVersion =
      recoveryCommand?.context.learnerStateVersion ?? projection.learnerStateVersion;
    const contextNoticeCopy = recoveryAskAvailable
      ? `${projection.contextNoticeCopy} 之前的问题还在等结果,再发一次就会接着确认。`.trim()
      : projection.contextNoticeCopy;
    this.setData({
      ...projection,
      ...refreshingPatch,
      askAvailable,
      profileAskAvailable: projection.askAvailable,
      recoveryAskAvailable,
      profileSourceStatus: runtime.profileSource.status,
      todaySourceStatus: runtime.todaySource.status,
      learnerStateVersion,
      profileLearnerStateVersion: projection.learnerStateVersion,
      contextNoticeCopy,
      ...(recoveryAskAvailable
        ? { stateLabel: stateLabels.provider_pending }
        : projection.contextStatus === "loading"
          ? { stateLabel: "正在看看你的学习情况" }
          : projection.askAvailable
            ? { stateLabel: "可以接着问学习、计划或岗位的事" }
            : { stateLabel: "学习记录没读出来" }),
      insightDismissed: false,
    });
  },

  syncAskAvailability() {
    const auth = currentAuthSession();
    if (auth) {
      const { reset } = contextRuntimeForSession(this, auth);
      if (reset) {
        this.applyContextProjection({ profileRefreshing: false, todayRefreshing: false });
        return;
      }
    }
    const recoveryCommand = auth ? recoverableCompanionCommand(auth.userId, this.data) : null;
    const recoveryAskAvailable = recoveryCommand !== null;
    const runtime = contextRuntimeFor(this);
    const projection = companionContextView(runtime.profileSource, runtime.todaySource);
    this.setData({
      askAvailable: this.data.profileAskAvailable || recoveryAskAvailable,
      recoveryAskAvailable,
      learnerStateVersion:
        recoveryCommand?.context.learnerStateVersion ?? this.data.profileLearnerStateVersion,
      contextNoticeCopy: recoveryAskAvailable
        ? `${projection.contextNoticeCopy} 之前的问题还在等结果,再发一次就会接着确认。`.trim()
        : projection.contextNoticeCopy,
    });
  },

  onKeyboardHeight(event: WechatMiniprogram.TextareaKeyboardHeightChange) {
    if (!this.data.manualKeyboard) return;
    const height = event.detail.height;
    // fixed 定位的输入坞在 iPad 上不随 adjust-position 上移，手动抬升。
    this.setData({ keyboardOffset: height > 0 ? height : 0 });
  },

  inputQuestion(event: WechatMiniprogram.Input) {
    this.setData({ question: event.detail.value });
    this.syncAskAvailability();
  },

  choosePrompt(event: WechatMiniprogram.BaseEvent) {
    const question = String(event.currentTarget.dataset.question ?? "").trim();
    if (question) {
      this.setData({ question });
      this.syncAskAvailability();
    }
  },

  async ask() {
    const auth = currentAuthSession();
    const client = requireAuthenticatedClient();
    if (!auth || !client || this.data.submitting) return;
    const question = this.data.question.trim();
    if (!/[\u3400-\u9fffA-Za-z0-9]/u.test(question)) {
      this.setData({ errorMessage: "写一个词或一句话，让岸岸知道你想问什么。" });
      return;
    }
    const recoveryCommand = recoverableCompanionCommand(auth.userId, {
      ...this.data,
      question,
    });
    if (!this.data.profileAskAvailable && !recoveryCommand) return;
    const learnerStateVersion = (
      recoveryCommand?.context.learnerStateVersion ?? this.data.profileLearnerStateVersion
    ).trim();
    if (!learnerStateVersion) return;
    const currentCommand = companionAskCommand(question, {
      routeId: this.data.askRouteId,
      workspace: this.data.askWorkspace,
      objectRefs: this.data.askObjectRefs,
      taskMode: this.data.askTaskMode,
      taskStage: this.data.askTaskStage,
      learnerStateVersion,
    });
    this.setData({
      submitting: true,
      askedQuestion: question,
      question: "",
      askAvailable: this.data.profileAskAvailable,
      recoveryAskAvailable: false,
      learnerStateVersion: this.data.profileLearnerStateVersion,
      errorMessage: "",
      requestId: "",
      answer: null,
      companionIntent: "thinking",
      stateLabel: "马上就好,别急",
    });
    // 聊天语义：问题气泡滚动到视口顶部，回答在其下方展开。
    void wx
      .pageScrollTo({ selector: "#ask-exchange", offsetTop: -12, duration: 240 })
      .catch(() => undefined);
    const attempts = new OperationAttemptStore(auth.userId);
    let attempt: Awaited<ReturnType<OperationAttemptStore["begin"]>> | undefined;
    try {
      const command = companionCommandForRetry(attempts, currentCommand);
      cancelChangedCompanionAttempt(attempts, command);
      attempt = await attempts.begin({
        scope: companionAskScope,
        canonicalInput: command,
        idempotencyKeyPrefix: "companion",
        expiresAtMs: Date.now() + companionAttemptTtlMs,
      });
      const { data } = await askCompanionWithOutcomeRecovery(
        new CompanionApi(client),
        command,
        attempt.idempotencyKey,
      );
      attempts.markSucceeded(companionAskScope, attempt.idempotencyKey);
      if (!sameSessionOwner(currentAuthSession(), auth)) {
        this.recoverExpiredSession();
        return;
      }
      this.setData({
        submitting: false,
        askAvailable: this.data.profileAskAvailable,
        recoveryAskAvailable: false,
        learnerStateVersion: this.data.profileLearnerStateVersion,
        semanticState: data.semanticState,
        stateLabel: stateLabels[data.semanticState],
        companionIntent: data.companionIntent,
        answer: companionAnswerView(data, stateLabels[data.semanticState]),
      });
    } catch (error) {
      const unknownOutcome = isTransportUnknownOutcome(error);
      if (attempt) {
        if (unknownOutcome)
          attempts.markTransportUnknown(companionAskScope, attempt.idempotencyKey);
        else attempts.markTerminal(companionAskScope, attempt.idempotencyKey);
      }
      if (!sameSessionOwner(currentAuthSession(), auth)) {
        this.recoverExpiredSession();
        return;
      }
      const view = unknownOutcome ? transportUnknownView(error) : companionFailureView(error);
      this.setData({
        submitting: false,
        askAvailable: this.data.profileAskAvailable,
        recoveryAskAvailable: false,
        learnerStateVersion: this.data.profileLearnerStateVersion,
        semanticState: view.semanticState,
        stateLabel: stateLabels[view.semanticState],
        companionIntent: "reminder",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  restoreAskedQuestion() {
    const question = this.data.askedQuestion.trim();
    if (!question) return;
    this.setData({
      question,
      askedQuestion: "",
      errorMessage: "",
      requestId: "",
      semanticState: "ready",
      stateLabel: "改改再发一次就好",
      companionIntent: "companion",
    });
    this.syncAskAvailability();
  },

  recoverExpiredSession() {
    // A 401 clears the exact session that owned the request. Do not leave the
    // composer spinning while this page is still bound to that expired owner.
    if (currentAuthSession()) return;
    this.setData({ submitting: false, askAvailable: false });
    void wx.reLaunch({ url: routes.login });
  },

  retryContext() {
    void this.loadContext();
  },

  runPrimaryInsight() {
    const action = this.data.insightAction;
    if (action === "today") {
      void wx.switchTab({ url: routes.today });
      return;
    }
    const url =
      action === "mastery"
        ? routes.xingceMastery
        : action === "positions"
          ? routes.positions
          : routes.dayAdjustment;
    void wx.navigateTo({ url });
  },

  dismissInsight() {
    this.setData({ insightDismissed: true });
  },

  openToday() {
    void wx.switchTab({ url: routes.today });
  },

  openMastery() {
    void wx.navigateTo({ url: routes.xingceMastery });
  },

  openWeeklyPlan() {
    void wx.navigateTo({ url: routes.weeklyPlan });
  },

  openPositions() {
    void wx.navigateTo({ url: routes.positions });
  },

  handleAction(event: WechatMiniprogram.BaseEvent) {
    const actionIndex = Number(event.currentTarget.dataset.index);
    const action =
      Number.isSafeInteger(actionIndex) && actionIndex >= 0
        ? this.data.answer?.allowedActions[actionIndex]
        : undefined;
    if (!action) return;
    const resolution = resolveCompanionAction(action);
    if (resolution.kind === "show_evidence") {
      void wx.showToast({ title: "理由都写在回答下面啦", icon: "none" });
      return;
    }
    if (resolution.kind === "refresh_context") {
      this.setData({ answer: null });
      void this.loadContext();
      return;
    }
    if (resolution.kind === "unavailable") {
      void wx.showToast({ title: "这个操作暂时用不了,过会儿再试", icon: "none" });
      return;
    }
    const navigate = () => {
      if (resolution.target.mode === "switchTab") {
        void wx.switchTab({ url: resolution.target.url });
      } else {
        void wx.navigateTo({ url: resolution.target.url });
      }
    };
    if (resolution.requiresConfirmation) {
      wx.showModal({
        title: "先确认一下",
        content: "岸岸的建议不会自己改你的计划,需要你在下一页再确认一次。",
        success: ({ confirm }) => {
          if (confirm) navigate();
        },
      });
      return;
    }
    navigate();
  },
});

async function askCompanionWithOutcomeRecovery(
  api: CompanionApi,
  command: CompanionAskRequest,
  idempotencyKey: string,
): Promise<{ data: CompanionAskResponse }> {
  try {
    return await api.ask(command, idempotencyKey);
  } catch (error) {
    // The provider result may already be committed even if response rendering or
    // the reverse proxy fails afterwards. Confirm once with the same key so the
    // backend replays the stored answer instead of charging for another call or
    // showing a false failure card.
    if (!(error instanceof ApiClientError) || !isRecoverableCompanionHttpStatus(error.statusCode)) {
      throw error;
    }
    return api.ask(command, idempotencyKey);
  }
}

function isRecoverableCompanionHttpStatus(statusCode: number): boolean {
  return statusCode === 408 || (statusCode >= 500 && statusCode <= 599);
}

function settledContextSource<T>(
  result: PromiseSettledResult<T>,
  previous: CompanionContextSourceState<T>,
  fallback: string,
): CompanionContextSourceState<T> {
  if (result.status === "fulfilled") {
    return { status: "ready", value: result.value };
  }
  const view = errorView(result.reason, fallback);
  if (contextSourceHasValue(previous)) {
    return {
      status: "stale",
      value: previous.value,
      message: view.message,
      requestId: view.requestId,
    };
  }
  return {
    status: "unavailable",
    message: view.message,
    requestId: view.requestId,
  };
}

async function loadTodayDashboardIfReady(client: ApiClient): Promise<TodayDashboard | null> {
  const { data: progress } = await new OnboardingApi(client).getProgress();
  if (!progress.studyPreferenceReady) return null;
  const { data } = await new PlanningApi(client).getToday();
  return data;
}

function settledTodayContextSource(
  result: PromiseSettledResult<TodayDashboard | null>,
  previous: CompanionContextSourceState<TodayDashboard>,
  fallback: string,
): CompanionContextSourceState<TodayDashboard> {
  if (result.status === "fulfilled") {
    return result.value
      ? { status: "ready", value: result.value }
      : {
          status: "unavailable",
          message: "先把每天的学习时间定下来,岸岸就能帮你安排今天的计划啦。",
          requestId: "",
        };
  }
  return settledContextSource(result, previous, fallback);
}

function contextSourceHasValue<T>(
  source: CompanionContextSourceState<T>,
): source is Extract<CompanionContextSourceState<T>, { status: "ready" | "stale" }> {
  return source.status === "ready" || source.status === "stale";
}

function companionAskCommand(
  questionText: string,
  context: {
    routeId: string;
    workspace: CompanionAskRequest["context"]["workspace"];
    objectRefs: NonNullable<CompanionAskRequest["context"]["objectRefs"]>;
    taskMode: CompanionAskRequest["context"]["taskMode"] | undefined;
    taskStage: CompanionAskRequest["context"]["taskStage"] | undefined;
    learnerStateVersion: string;
  },
): CompanionAskRequest {
  return {
    questionText,
    context: {
      routeId: context.routeId,
      workspace: context.workspace,
      objectRefs: [...context.objectRefs],
      ...(context.taskMode && context.taskStage
        ? { taskMode: context.taskMode, taskStage: context.taskStage }
        : {}),
      learnerStateVersion: context.learnerStateVersion,
    },
  };
}

function recoverableCompanionCommand(
  ownerUserId: string,
  draft: CompanionAskDraft,
): CompanionAskRequest | null {
  const attempt = new OperationAttemptStore(ownerUserId).load(companionAskScope);
  if (
    !attempt ||
    (attempt.state !== "pending" && attempt.state !== "unknown") ||
    !isCompanionAskRequest(attempt.canonicalInput)
  ) {
    return null;
  }
  const command = attempt.canonicalInput;
  if (command.questionText !== draft.question.trim()) return null;
  const currentContext: CompanionAskRequest["context"] = {
    routeId: draft.askRouteId,
    workspace: draft.askWorkspace,
    objectRefs: [...draft.askObjectRefs],
    ...(draft.askTaskMode && draft.askTaskStage
      ? { taskMode: draft.askTaskMode, taskStage: draft.askTaskStage }
      : {}),
    // learnerStateVersion 不是用户选择项；恢复时只能沿用持久化 command 的冻结版本。
    learnerStateVersion: command.context.learnerStateVersion,
  };
  return sameUserSelectedCompanionContext(command.context, currentContext) ? command : null;
}

function cancelChangedCompanionAttempt(
  attempts: OperationAttemptStore,
  canonicalInput: CompanionAskRequest,
): void {
  const existing = attempts.load(companionAskScope);
  if (
    existing &&
    (existing.state === "pending" || existing.state === "unknown") &&
    existing.canonicalFingerprint !== stableFingerprint(canonicalInput)
  ) {
    attempts.cancel(companionAskScope, existing.idempotencyKey);
  }
}

function companionCommandForRetry(
  attempts: OperationAttemptStore,
  currentCommand: CompanionAskRequest,
): CompanionAskRequest {
  const existing = attempts.load(companionAskScope);
  if (
    !existing ||
    (existing.state !== "pending" && existing.state !== "unknown") ||
    !isCompanionAskRequest(existing.canonicalInput) ||
    existing.canonicalInput.questionText !== currentCommand.questionText ||
    !sameUserSelectedCompanionContext(existing.canonicalInput.context, currentCommand.context)
  ) {
    return currentCommand;
  }
  return existing.canonicalInput;
}

function sameUserSelectedCompanionContext(
  persisted: CompanionAskRequest["context"],
  current: CompanionAskRequest["context"],
): boolean {
  return (
    persisted.routeId === current.routeId &&
    persisted.workspace === current.workspace &&
    persisted.taskMode === current.taskMode &&
    persisted.taskStage === current.taskStage &&
    stableFingerprint(persisted.objectRefs ?? []) === stableFingerprint(current.objectRefs ?? [])
  );
}

function isTransportUnknownOutcome(error: unknown): boolean {
  // 只有客户端没有收到任何 HTTP 响应时，服务端是否执行过才真正未知。
  // 明确收到的 5xx 也是一次已结束的失败，不能把同一问题和 key 锁住七天。
  return error instanceof ApiClientError && error.classification === "transport_unknown";
}

function transportUnknownView(error: unknown): {
  semanticState: CompanionAskResponse["semanticState"];
  message: string;
  requestId: string;
} {
  return {
    semanticState: "provider_pending",
    message: companionPendingMessage,
    requestId: error instanceof ApiClientError ? error.requestId : "",
  };
}

function isCompanionAskRequest(value: unknown): value is CompanionAskRequest {
  if (!hasExactKeys(value, ["context", "questionText"])) return false;
  if (
    !isBoundedTrimmedString(value.questionText, 800) ||
    !/[\u3400-\u9fffA-Za-z0-9]/u.test(value.questionText)
  ) {
    return false;
  }
  const context = value.context;
  if (!isPlainObject(context)) return false;
  const hasTaskMode = Object.prototype.hasOwnProperty.call(context, "taskMode");
  const hasTaskStage = Object.prototype.hasOwnProperty.call(context, "taskStage");
  if (hasTaskMode !== hasTaskStage) return false;
  const expectedKeys = ["learnerStateVersion", "objectRefs", "routeId", "workspace"];
  if (hasTaskMode) expectedKeys.push("taskMode", "taskStage");
  if (!hasExactKeys(context, expectedKeys)) return false;
  if (
    !isBoundedTrimmedString(context.routeId, 200) ||
    !isBoundedTrimmedString(context.learnerStateVersion, 200) ||
    !isCompanionWorkspace(context.workspace) ||
    !Array.isArray(context.objectRefs) ||
    context.objectRefs.length > 12 ||
    !context.objectRefs.every(isCompanionObjectRef)
  ) {
    return false;
  }
  const identities = context.objectRefs.map((item) => stableFingerprint(item));
  if (new Set(identities).size !== identities.length) return false;
  return (
    !hasTaskMode ||
    (isCompanionTaskMode(context.taskMode) && isCompanionTaskStage(context.taskStage))
  );
}

function isCompanionObjectRef(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["objectRefs"]>[number] {
  if (!hasExactKeys(value, ["id", "type", "version"])) return false;
  return (
    isCompanionObjectType(value.type) &&
    isBoundedTrimmedString(value.id, 200) &&
    isBoundedTrimmedString(value.version, 200)
  );
}

function isCompanionWorkspace(
  value: unknown,
): value is CompanionAskRequest["context"]["workspace"] {
  return value === "today" || value === "training" || value === "position" || value === "profile";
}

function isCompanionTaskMode(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["taskMode"]> {
  return value === "practice" || value === "retest" || value === "mock" || value === "essay";
}

function isCompanionTaskStage(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["taskStage"]> {
  return (
    value === "reading" || value === "answering" || value === "submitted" || value === "reviewing"
  );
}

function isCompanionObjectType(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["objectRefs"]>[number]["type"] {
  return (
    value === "position" ||
    value === "question" ||
    value === "material" ||
    value === "attempt" ||
    value === "plan" ||
    value === "report"
  );
}

function isBoundedTrimmedString(value: unknown, maxLength: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    value.trim() === value
  );
}

function hasExactKeys(
  value: unknown,
  expectedKeys: readonly string[],
): value is Record<string, unknown> {
  if (!isPlainObject(value)) return false;
  const keys = Object.keys(value);
  return (
    keys.length === expectedKeys.length &&
    expectedKeys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype: unknown = Object.getPrototypeOf(value) as unknown;
  return prototype === Object.prototype || prototype === null;
}
