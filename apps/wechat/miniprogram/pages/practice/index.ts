import { ApiClient, ApiClientError } from "../../api/client";
import { XingceLearningApi, type XingceTaskSession } from "../../api/xingce-learning";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { createCoachRepository } from "../../data/coach-repository";
import {
  checkpointCommand,
  mergePendingDraft,
  resolveXingcePlanTaskId,
  runtimeFromXingceSession,
  runtimeWithPendingCheckpoint,
} from "../../features/xingce/real_attempt/controller";
import {
  isValidPlanTaskId,
  parseXingceTaskMode,
} from "../../features/xingce/real_attempt/contracts";
import { XingcePageRuntimeRegistry } from "../../features/xingce/real_attempt/page-runtime";
import {
  presentXingceResult,
  presentXingceTask,
  type XingceResultView,
  type XingceTaskView,
} from "../../features/xingce/real_attempt/presentation";
import {
  clearPendingXingceCheckpoint,
  clearPendingXingceStart,
  clearPendingXingceSubmit,
  loadPendingXingceCheckpoint,
  loadPendingXingceStart,
  loadPendingXingceSubmit,
  storePendingXingceCheckpoint,
  storePendingXingceStart,
  storePendingXingceSubmit,
} from "../../features/xingce/real_attempt/pending-store";
import { publishLatestTodayDashboard } from "../../features/planning/runtime-state";
import { createIdempotencyKey } from "../../platform/command-id";
import { reduceTaskRuntime, unansweredQuestionIds } from "../../task-runtime/state/runtime";

type PageStatus = "loading" | "ready" | "error" | "unavailable";

interface PracticePageData {
  status: PageStatus;
  errorTitle: string;
  errorMessage: string;
  showLoginAction: boolean;
  view: XingceTaskView | null;
  result: XingceResultView | null;
  sessionState: XingceTaskSession["state"] | "";
  materialOpen: boolean;
  answerSheetOpen: boolean;
  interactionLocked: boolean;
  canSubmit: boolean;
  hasPendingCheckpoint: boolean;
  pendingCheckpointMessage: string;
  answerSheetItems: Array<{
    index: number;
    label: string;
    state: "answered" | "skipped" | "unanswered";
    active: boolean;
  }>;
  confidenceOptions: Array<{ value: "low" | "medium" | "high"; label: string }>;
}

interface PracticePageCustom {
  loadTask(): Promise<void>;
  retry(): void;
  goLogin(): void;
  exit(): Promise<void>;
  toggleMaterial(): void;
  toggleAnswerSheet(): void;
  selectQuestion(event: WechatMiniprogram.BaseEvent): void;
  selectOption(event: WechatMiniprogram.BaseEvent): void;
  toggleEliminated(event: WechatMiniprogram.BaseEvent): void;
  selectConfidence(event: WechatMiniprogram.BaseEvent): void;
  toggleMarked(): void;
  skipQuestion(): void;
  inputDraft(event: WechatMiniprogram.Input): void;
  previousQuestion(): void;
  nextQuestion(): void;
  submitAttempt(): Promise<void>;
  openReflection(): void;
  returnToday(): void;
  retryPendingCheckpoint(): Promise<void>;
  noop(): void;
}

type PracticePageInstance = WechatMiniprogram.Page.Instance<PracticePageData, PracticePageCustom>;

const runtimes = new XingcePageRuntimeRegistry<PracticePageInstance>();

Page<PracticePageData, PracticePageCustom>({
  data: {
    status: "loading",
    errorTitle: "",
    errorMessage: "",
    showLoginAction: false,
    view: null,
    result: null,
    sessionState: "",
    materialOpen: false,
    answerSheetOpen: false,
    interactionLocked: false,
    canSubmit: false,
    hasPendingCheckpoint: false,
    pendingCheckpointMessage: "",
    answerSheetItems: [],
    confidenceOptions: [
      { value: "low", label: "不确定" },
      { value: "medium", label: "比较确定" },
      { value: "high", label: "非常确定" },
    ],
  },

  onLoad(options: Record<string, string | undefined>) {
    const runtime = runtimes.attach(this);
    runtime.requestedMode = parseXingceTaskMode(options.mode);
    runtime.planTaskId = isValidPlanTaskId(options.planTaskId) ? options.planTaskId.trim() : "";
    if (!runtime.requestedMode) {
      failPage(this, "任务地址不完整", "缺少明确的行测训练模式，页面不会猜测要执行的任务。");
      return;
    }
    if (environment.dataMode === "prototype") {
      this.setData({
        status: "unavailable",
        errorTitle: "演示数据不能进入真实作答",
        errorMessage: "行测题组、答案版本与复盘证据必须由已认证服务端提供。",
      });
      return;
    }
    const auth = currentAuthSession();
    if (!auth) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能开始行测任务",
        errorMessage: "作答断点与复盘证据必须绑定你的账号。",
        showLoginAction: true,
      });
      return;
    }
    runtime.ownerUserId = auth.userId;
    runtime.api = new XingceLearningApi(new ApiClient(environment.apiBaseUrl, auth.accessToken));
    void this.loadTask();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(this, runtime);
      return;
    }
    if (!runtime.session) return;
    if (runtime.session.state !== "answering") void this.loadTask();
    else startTimer(this, runtime);
  },

  onHide() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    stopTimer(runtime);
    if (runtime.task?.syncState === "pending") void saveCheckpoint(this, runtime);
  },

  onUnload() {
    runtimes.detach(this);
  },

  async loadTask() {
    const runtime = runtimes.get(this);
    if (!runtime?.api || !runtime.requestedMode) return;
    const epoch = runtimes.beginRequest(runtime);
    this.setData({
      status: "loading",
      errorTitle: "",
      errorMessage: "",
      showLoginAction: false,
    });
    try {
      if (runtime.session) {
        const response = await runtime.api.getSession(runtime.session.sessionId);
        if (!runtimes.isCurrent(this, runtime, epoch)) return;
        if (!currentAccountOwns(runtime)) {
          invalidateForAccountChange(this, runtime);
          return;
        }
        applySession(this, runtime, response.data);
        return;
      }
      if (!runtime.planTaskId) {
        const dashboard = await createCoachRepository(currentAuthSession()?.accessToken).getToday();
        if (!runtimes.isCurrent(this, runtime, epoch)) return;
        if (!currentAccountOwns(runtime)) {
          invalidateForAccountChange(this, runtime);
          return;
        }
        runtime.planTaskId = resolveXingcePlanTaskId(dashboard, runtime.requestedMode);
      }
      let pending = loadPendingXingceStart(runtime.ownerUserId, runtime.planTaskId);
      if (!pending) {
        pending = {
          kind: "start",
          planTaskId: runtime.planTaskId,
          mode: runtime.requestedMode,
          idempotencyKey: await createIdempotencyKey("xingce-start"),
        };
        storePendingXingceStart(runtime.ownerUserId, pending);
      }
      const response = await runtime.api.startSession(pending.planTaskId, pending.idempotencyKey);
      if (!runtimes.isCurrent(this, runtime, epoch)) return;
      if (!currentAccountOwns(runtime)) {
        invalidateForAccountChange(this, runtime);
        return;
      }
      clearPendingXingceStart(runtime.ownerUserId, runtime.planTaskId);
      applySession(this, runtime, response.data);
    } catch (error) {
      if (!runtimes.isCurrent(this, runtime, epoch)) return;
      handlePageError(this, error, "真实行测任务尚未载入");
    }
  },

  retry() {
    void this.loadTask();
  },

  goLogin() {
    void wx.redirectTo({ url: routes.login });
  },

  async exit() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    if (
      runtime.session &&
      ["submitted", "reflection_required", "cause_decision_required"].includes(
        runtime.session.state,
      )
    ) {
      const result = await wx.showModal({
        title: "复盘尚未完成",
        content: "本次结果已经记录，但完成原话复盘前不会结算今日任务。你可以稍后从今日计划继续。",
        confirmText: "仍要退出",
      });
      if (!result.confirm) return;
    } else if (runtime.task?.syncState === "pending") {
      await saveCheckpoint(this, runtime);
      const currentTask = runtimes.get(this)?.task;
      if (currentTask?.syncState !== "saved") return;
    }
    void wx.navigateBack();
  },

  toggleMaterial() {
    this.setData({ materialOpen: !this.data.materialOpen });
  },

  toggleAnswerSheet() {
    this.setData({ answerSheetOpen: !this.data.answerSheetOpen });
  },

  selectQuestion(event) {
    const runtime = runtimes.get(this);
    const index = Number(event.currentTarget.dataset.index ?? -1);
    if (!runtime?.task || this.data.interactionLocked) return;
    runtime.task = reduceTaskRuntime(runtime.task, { type: "QUESTION_CHANGED", index });
    this.setData({ answerSheetOpen: false });
    render(this, runtime);
    void saveCheckpointIfPossible(this, runtime);
  },

  selectOption(event) {
    const runtime = runtimes.get(this);
    const optionId = String(event.currentTarget.dataset.optionid ?? "");
    if (!runtime?.task || !optionId || this.data.interactionLocked) return;
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "ANSWER_SELECTED",
      optionIds: [optionId],
    });
    render(this, runtime);
    void saveCheckpoint(this, runtime);
  },

  toggleEliminated(event) {
    const runtime = runtimes.get(this);
    const optionId = String(event.currentTarget.dataset.optionid ?? "");
    if (!runtime?.task || !optionId || this.data.interactionLocked) return;
    const questionId = runtime.task.questionOrder[runtime.task.currentQuestionIndex];
    const attempt = questionId ? runtime.task.attempts[questionId] : undefined;
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "OPTION_ELIMINATED",
      optionId,
      eliminated: !(attempt?.eliminatedOptionIds.includes(optionId) ?? false),
    });
    render(this, runtime);
    void saveCheckpointIfPossible(this, runtime);
  },

  selectConfidence(event) {
    const runtime = runtimes.get(this);
    const confidence = String(event.currentTarget.dataset.confidence ?? "");
    if (
      !runtime?.task ||
      this.data.interactionLocked ||
      (confidence !== "low" && confidence !== "medium" && confidence !== "high")
    )
      return;
    runtime.task = reduceTaskRuntime(runtime.task, { type: "CONFIDENCE_SET", confidence });
    render(this, runtime);
    void saveCheckpointIfPossible(this, runtime);
  },

  toggleMarked() {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    const questionId = runtime.task.questionOrder[runtime.task.currentQuestionIndex];
    const attempt = questionId ? runtime.task.attempts[questionId] : undefined;
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "QUESTION_MARKED",
      marked: !(attempt?.marked ?? false),
    });
    render(this, runtime);
    void saveCheckpointIfPossible(this, runtime);
  },

  skipQuestion() {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    runtime.task = reduceTaskRuntime(runtime.task, { type: "QUESTION_SKIPPED" });
    render(this, runtime);
    void saveCheckpoint(this, runtime);
  },

  inputDraft(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "DRAFT_CHANGED",
      text: event.detail.value,
    });
    render(this, runtime);
  },

  previousQuestion() {
    changeQuestion(this, -1);
  },

  nextQuestion() {
    changeQuestion(this, 1);
  },

  async submitAttempt() {
    const runtime = runtimes.get(this);
    if (!runtime?.api || !runtime.task || !runtime.session || runtime.submitInFlight) return;
    const missing = unansweredQuestionIds(runtime.task);
    if (missing.length > 0) {
      this.setData({ answerSheetOpen: true });
      void wx.showToast({ title: "请逐题作答或明确跳过", icon: "none" });
      return;
    }
    if (runtime.task.syncState !== "saved") await saveCheckpoint(this, runtime);
    if (runtime.task.syncState !== "saved") return;
    runtime.submitInFlight = true;
    this.setData({ interactionLocked: true });
    try {
      let pending = loadPendingXingceSubmit(runtime.ownerUserId, runtime.session.sessionId);
      if (!pending) {
        pending = {
          kind: "submit",
          planTaskId: runtime.planTaskId,
          sessionId: runtime.session.sessionId,
          idempotencyKey: await createIdempotencyKey("xingce-submit"),
          command: {
            expectedRevision: runtime.task.revision,
            totalElapsedSeconds: runtime.task.totalElapsedSeconds,
          },
        };
        storePendingXingceSubmit(runtime.ownerUserId, pending);
      }
      const response = await runtime.api.submitAttempt(
        pending.sessionId,
        pending.command,
        pending.idempotencyKey,
      );
      clearPendingXingceSubmit(runtime.ownerUserId, pending.sessionId);
      applySession(this, runtime, response.data);
      stopTimer(runtime);
    } catch (error) {
      if (error instanceof ApiClientError && error.statusCode === 409) {
        clearPendingXingceSubmit(runtime.ownerUserId, runtime.session.sessionId);
        try {
          const current = await runtime.api.getSession(runtime.session.sessionId);
          applySession(this, runtime, current.data);
          this.setData({
            errorMessage: "服务端版本已更新，已加载权威断点。请确认当前答题卡后重新交卷。",
          });
          return;
        } catch (reloadError) {
          handleInlineError(this, runtime, reloadError, "无法重新加载服务端权威断点。");
          return;
        }
      }
      handleInlineError(this, runtime, error, "交卷结果尚未确认，请重试同一命令。");
    } finally {
      runtime.submitInFlight = false;
      if (runtimes.owns(this, runtime)) this.setData({ interactionLocked: false });
    }
  },

  openReflection() {
    const runtime = runtimes.get(this);
    const reviewId = runtime?.session?.review?.reviewId;
    const sessionId = runtime?.session?.sessionId;
    if (!reviewId || !sessionId) return;
    const url =
      routes.reflection +
      "?sessionId=" +
      encodeURIComponent(sessionId) +
      "&reviewId=" +
      encodeURIComponent(reviewId);
    void wx.navigateTo({ url });
  },

  returnToday() {
    const plan = runtimes.get(this)?.session?.completion?.plan;
    if (plan) publishLatestTodayDashboard(plan);
    void wx.switchTab({ url: routes.today });
  },

  async retryPendingCheckpoint() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    await saveCheckpoint(this, runtime);
  },

  noop() {},
});

function applySession(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
  session: XingceTaskSession,
): void {
  runtime.session = session;
  runtime.planTaskId = session.planTaskId;
  const pending = loadPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
  const retryablePending = session.state === "answering" ? pending : null;
  if (pending && !retryablePending) {
    clearPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
  }
  runtime.task = retryablePending
    ? runtimeWithPendingCheckpoint(session, retryablePending.command)
    : runtimeFromXingceSession(session);
  page.setData({
    hasPendingCheckpoint: retryablePending !== null,
    pendingCheckpointMessage: retryablePending
      ? "检测到一次结果未知的断点保存，已恢复当时的本地草稿；请按原命令重试。"
      : "",
    interactionLocked: retryablePending !== null,
  });
  render(page, runtime);
  if (session.state === "answering") startTimer(page, runtime);
  else stopTimer(runtime);
}

function render(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): void {
  if (!runtime.session || !runtime.task) return;
  const missing = unansweredQuestionIds(runtime.task);
  const answerSheetItems = runtime.task.questionOrder.map((questionId, index) => {
    const attempt = runtime.task?.attempts[questionId];
    return {
      index,
      label: String(index + 1),
      state: attempt?.skipped
        ? ("skipped" as const)
        : (attempt?.selectedOptionIds.length ?? 0) > 0
          ? ("answered" as const)
          : ("unanswered" as const),
      active: index === runtime.task?.currentQuestionIndex,
    };
  });
  page.setData({
    status: "ready",
    errorTitle: "",
    errorMessage: "",
    view: presentXingceTask(runtime.session, runtime.task),
    result: presentXingceResult(runtime.session),
    sessionState: runtime.session.state,
    canSubmit: runtime.session.state === "answering" && missing.length === 0,
    answerSheetItems,
  });
}

function changeQuestion(page: PracticePageInstance, delta: number): void {
  const runtime = runtimes.get(page);
  if (!runtime?.task || page.data.interactionLocked) return;
  const next = runtime.task.currentQuestionIndex + delta;
  if (next < 0 || next >= runtime.task.questionOrder.length) return;
  runtime.task = reduceTaskRuntime(runtime.task, { type: "QUESTION_CHANGED", index: next });
  render(page, runtime);
  void saveCheckpointIfPossible(page, runtime);
}

async function saveCheckpointIfPossible(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): Promise<void> {
  try {
    checkpointCommand(runtime.task as NonNullable<typeof runtime.task>);
  } catch {
    return;
  }
  await saveCheckpoint(page, runtime);
}

async function saveCheckpoint(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
  conflictRetry = true,
): Promise<void> {
  if (!runtime.api || !runtime.task || !runtime.session || runtime.saveInFlight) return;
  runtime.saveInFlight = true;
  stopTimer(runtime);
  runtime.task = reduceTaskRuntime(runtime.task, { type: "SAVE_REQUESTED" });
  render(page, runtime);
  page.setData({ interactionLocked: true });
  const localDraft = runtime.task;
  let commandConfirmed = false;
  try {
    let pending = loadPendingXingceCheckpoint(runtime.ownerUserId, runtime.session.sessionId);
    if (!pending) {
      pending = {
        kind: "checkpoint",
        planTaskId: runtime.planTaskId,
        sessionId: runtime.session.sessionId,
        idempotencyKey: await createIdempotencyKey("xingce-save"),
        command: checkpointCommand(runtime.task),
      };
      storePendingXingceCheckpoint(runtime.ownerUserId, pending);
    }
    await runtime.api.saveCheckpoint(pending.sessionId, pending.command, pending.idempotencyKey);
    commandConfirmed = true;
    clearPendingXingceCheckpoint(runtime.ownerUserId, pending.sessionId);
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(page, runtime);
      return;
    }
    const current = await runtime.api.getSession(pending.sessionId);
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(page, runtime);
      return;
    }
    page.setData({ hasPendingCheckpoint: false, pendingCheckpointMessage: "" });
    applySession(page, runtime, current.data);
  } catch (error) {
    if (commandConfirmed) {
      page.setData({
        status: "error",
        errorTitle: "断点已保存，但当前会话刷新失败",
        errorMessage: "服务端已确认原命令；请重新加载当前权威会话后再继续。",
        hasPendingCheckpoint: false,
        pendingCheckpointMessage: "",
        interactionLocked: true,
      });
      return;
    }
    if (error instanceof ApiClientError && error.statusCode === 409 && conflictRetry) {
      clearPendingXingceCheckpoint(runtime.ownerUserId, runtime.session.sessionId);
      const current = await runtime.api.getSession(runtime.session.sessionId);
      if (!currentAccountOwns(runtime)) {
        invalidateForAccountChange(page, runtime);
        return;
      }
      runtime.session = current.data;
      if (current.data.state !== "answering") {
        page.setData({ hasPendingCheckpoint: false, pendingCheckpointMessage: "" });
        applySession(page, runtime, current.data);
        return;
      }
      runtime.task = mergePendingDraft(current.data, localDraft);
      runtime.saveInFlight = false;
      await saveCheckpoint(page, runtime, false);
      return;
    }
    runtime.task = reduceTaskRuntime(runtime.task, {
      type:
        error instanceof ApiClientError && error.statusCode === 409
          ? "REVISION_CONFLICT"
          : "SAVE_FAILED",
      requestId: error instanceof ApiClientError ? error.requestId : "xingce-save-error",
    });
    render(page, runtime);
    handleInlineError(page, runtime, error, "断点尚未获得服务端确认，本地命令会保留供原样重试。");
    page.setData({
      hasPendingCheckpoint: true,
      pendingCheckpointMessage: "服务端尚未确认这次保存，必须按原命令重试后才能继续修改。",
      interactionLocked: true,
    });
  } finally {
    runtime.saveInFlight = false;
    if (runtimes.owns(page, runtime) && !page.data.hasPendingCheckpoint) {
      page.setData({ interactionLocked: false });
    }
  }
}

function startTimer(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): void {
  stopTimer(runtime);
  runtime.timerHandle = setInterval(() => {
    if (!runtimes.owns(page, runtime) || !runtime.task || runtime.task.phase !== "answering")
      return;
    runtime.task = reduceTaskRuntime(runtime.task, { type: "TICK", seconds: 1 });
    render(page, runtime);
  }, 1_000);
}

function stopTimer(runtime: NonNullable<ReturnType<typeof runtimes.get>>): void {
  if (runtime.timerHandle !== undefined) clearInterval(runtime.timerHandle);
  runtime.timerHandle = undefined;
}

function handlePageError(page: PracticePageInstance, error: unknown, title: string): void {
  const expired = error instanceof ApiClientError && error.statusCode === 401;
  page.setData({
    status: expired ? "unavailable" : "error",
    errorTitle: expired ? "登录状态已失效" : title,
    errorMessage: errorMessage(error),
    showLoginAction: expired,
  });
}

function handleInlineError(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
  error: unknown,
  fallback: string,
): void {
  const expired = error instanceof ApiClientError && error.statusCode === 401;
  if (expired) {
    page.setData({
      status: "unavailable",
      errorTitle: "登录状态已失效",
      errorMessage: "本地待提交命令仍按原账号隔离保存，重新登录后可以继续。",
      showLoginAction: true,
    });
    stopTimer(runtime);
    return;
  }
  page.setData({ errorMessage: error instanceof Error ? error.message : fallback });
  void wx.showToast({ title: fallback, icon: "none", duration: 2500 });
}

function failPage(page: PracticePageInstance, title: string, message: string): void {
  page.setData({ status: "error", errorTitle: title, errorMessage: message });
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "服务端没有返回可确认的真实状态。";
}

function currentAccountOwns(runtime: NonNullable<ReturnType<typeof runtimes.get>>): boolean {
  return currentAuthSession()?.userId === runtime.ownerUserId;
}

function invalidateForAccountChange(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): void {
  if (!runtimes.owns(page, runtime)) return;
  runtimes.detach(page);
  page.setData({
    status: "unavailable",
    errorTitle: "账号已经变更",
    errorMessage: "旧账号的题目与断点已从当前页面清除，请以当前账号重新进入今日计划。",
    showLoginAction: true,
    view: null,
    result: null,
    sessionState: "",
    interactionLocked: true,
  });
}
