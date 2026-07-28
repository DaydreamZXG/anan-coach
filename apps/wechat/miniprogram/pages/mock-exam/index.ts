import { ApiClient, ApiClientError } from "../../api/client";
import {
  MockExamApi,
  type MockExamCheckpointReceipt,
  type MockExamDisputeCategory,
  type MockExamDisputeReceipt,
  type MockExamLearnerMaterial,
  type MockExamLearnerQuestion,
  type MockExamLearnerSession,
  type MockExamSubmitReceipt,
  type MockExamTrajectoryCommand,
} from "../../api/mock-exam";
import { TaskEditingApi } from "../../api/task-editing";
import type { TaskEditOwnership } from "../../features/task-editing/coordinator";
import {
  acquireTaskEditOwnership,
  resolveRevisionConflict,
  TaskEditTakeoverDeclined,
} from "../../features/task-editing/coordinator";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { routes } from "../../app/navigation/routes";
import { isValidPlanTaskId } from "../../features/xingce/real_attempt/contracts";
import { TaskHelpApi, type TaskHelpCommand } from "../../api/task-help";
import { currentAuthSession, sameSessionOwner, type ActiveAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { createIdempotencyKey, stableFingerprint } from "../../platform/command-id";
import type { TaskRuntimeState } from "../../task-runtime/contracts";
import type { ContentDisplayPreferences } from "../../task-runtime/content-renderer/model";
import {
  loadTaskDisplayPreferences,
  storeTaskDisplayPreferences,
} from "../../task-runtime/display-preferences";
import {
  createMaterialReaderState,
  reduceMaterialReader,
  type MaterialReaderPosition,
  type MaterialReaderState,
} from "../../task-runtime/material-reader/state";
import { reduceTaskRuntime } from "../../task-runtime/state/runtime";
import {
  resolveSwipeNav,
  swipeSampleFromTouchEnd,
  swipeSampleFromTouchStart,
  type SwipeNavSample,
} from "../../task-runtime/swipe-nav";
import { taskToolPolicy, type TaskToolPolicy } from "../../task-runtime/tool-policy/index";
import {
  appendPendingMockTrajectory,
  clearPendingMockCheckpoint,
  clearPendingMockSubmit,
  loadPendingMockCheckpoint,
  loadPendingMockSubmit,
  loadPendingMockTrajectory,
  removePendingMockTrajectory,
  replacePendingMockTrajectory,
  storePendingMockCheckpoint,
  storePendingMockSubmit,
  type PendingMockCheckpoint,
  type PendingMockSubmit,
  type PendingMockTrajectoryEvent,
} from "./pending-store";
import {
  buildMockCheckpointCommand,
  buildMockSubmitCommand,
  countMockAnswers,
  formatRemainingTime,
  remainingMockSeconds,
  runtimeFromMockSession,
  runtimeWithPendingMockCheckpoint,
  runtimeWithPendingMockSubmit,
  runtimeWithServerRevision,
} from "./runtime";

type PageStatus = "loading" | "ready" | "submitted" | "error" | "unavailable";
type FontScale = "standard" | "large";

interface MockQuestionView extends Omit<MockExamLearnerQuestion, "options"> {
  options: Array<
    MockExamLearnerQuestion["options"][number] & { selected: boolean; eliminated: boolean }
  >;
  marked: boolean;
  draftText: string;
  material: MockExamLearnerMaterial | null;
}

interface MockAnswerSheetItem {
  index: number;
  label: string;
  state: "answered" | "unanswered" | "marked";
  active: boolean;
}

interface MockExamPageData {
  status: PageStatus;
  errorTitle: string;
  errorMessage: string;
  showLoginAction: boolean;
  title: string;
  question: MockQuestionView | null;
  current: number;
  total: number;
  answered: number;
  unanswered: number;
  marked: number;
  remainingLabel: string;
  syncLabel: string;
  answerSheetItems: MockAnswerSheetItem[];
  answerSheetOpen: boolean;
  draftOpen: boolean;
  materialOpen: boolean;
  toolsOpen: boolean;
  helpOpen: boolean;
  interactionLocked: boolean;
  fontScale: FontScale;
  eyeCare: boolean;
  contentPreferences: ContentDisplayPreferences;
  materialReaderState: MaterialReaderState;
  materialQuestionId: string;
  toolPolicy: TaskToolPolicy;
  pageObjectReference: TaskHelpCommand["pageObjectReference"] | null;
  helpStatus: "idle" | "submitting" | "resolved" | "error";
  helpMessage: string;
  disputeOpen: boolean;
  disputeCategories: Array<{ value: MockExamDisputeCategory; label: string }>;
  disputeCategory: MockExamDisputeCategory;
  disputeDescription: string;
  disputeStatus: "idle" | "submitting" | "resolved" | "error";
  disputeMessage: string;
  disputeReceipt: MockExamDisputeReceipt | null;
  receipt: MockExamSubmitReceipt | null;
  reportSessionId: string;
  /** Figma 98:173 交卷确认面板。 */
  submitConfirmOpen: boolean;
  submitElapsedLabel: string;
  submitMissingLabels: string[];
  /** 快滑切题的一次性转场方向（motion-question-in--*），动画结束即清空。 */
  questionTransition: "" | "prev" | "next";
}

interface MockExamPageCustom {
  loadExam(): Promise<void>;
  retry(): void;
  goLogin(): void;
  exit(): Promise<void>;
  selectOption(event: WechatMiniprogram.BaseEvent): void;
  toggleEliminated(event: WechatMiniprogram.BaseEvent): void;
  toggleMarked(): void;
  inputDraft(event: WechatMiniprogram.Input): void;
  previousQuestion(): void;
  nextQuestion(): void;
  onQuestionTouchStart(event: WechatMiniprogram.TouchEvent): void;
  onQuestionTouchMove(event: WechatMiniprogram.TouchEvent): void;
  onQuestionTouchEnd(event: WechatMiniprogram.TouchEvent): void;
  clearQuestionTransition(): void;
  selectQuestion(event: WechatMiniprogram.BaseEvent): void;
  toggleAnswerSheet(): void;
  toggleDraft(): void;
  toggleMaterial(): void;
  updateMaterialPosition(
    event: WechatMiniprogram.CustomEvent<{ position: MaterialReaderPosition }>,
  ): void;
  updateMaterialReaderState(
    event: WechatMiniprogram.CustomEvent<{ state: MaterialReaderState }>,
  ): void;
  toggleTools(): void;
  openFocusedTool(): void;
  openHelp(): void;
  closeHelp(): void;
  submitHelp(event: WechatMiniprogram.CustomEvent<TaskHelpCommand>): Promise<void>;
  setFontScale(event: WechatMiniprogram.BaseEvent): void;
  toggleEyeCare(): void;
  reportDispute(): void;
  closeDispute(): void;
  selectDisputeCategory(event: WechatMiniprogram.BaseEvent): void;
  inputDisputeDescription(event: WechatMiniprogram.Input): void;
  submitDispute(): Promise<void>;
  submitExam(): void;
  closeSubmitConfirm(): void;
  confirmSubmitExam(): Promise<void>;
  returnToday(): void;
  openMockReport(): void;
  noop(): void;
}

type MockExamPageInstance = WechatMiniprogram.Page.Instance<MockExamPageData, MockExamPageCustom>;

interface PageRuntime {
  page: MockExamPageInstance;
  authSession: ActiveAuthSession;
  ownerUserId: string;
  api: MockExamApi;
  helpApi: TaskHelpApi;
  editingApi: TaskEditingApi;
  editOwnership?: TaskEditOwnership;
  requestedSessionId: string;
  requestedPlanTaskId: string;
  session?: MockExamLearnerSession;
  task?: TaskRuntimeState;
  visible: boolean;
  disposed: boolean;
  timerId?: number;
  /** 契约增量 v1：毫秒时钟基准点（Date.now() 差值累计，后台/停表时冻结）。 */
  lastTickAtMs?: number;
  autosaveTimerId?: number;
  mutationVersion: number;
  dirtyQuestionIds: Set<string>;
  savePromise?: Promise<boolean>;
  trajectoryPromise?: Promise<boolean>;
  nextTrajectoryEventSeq: number;
  submitInFlight: boolean;
  /** 快滑切题手势的起点采样；多指或松手后置回 null。 */
  swipeStart: SwipeNavSample | null;
}

type MockSubmitOutcome =
  | { kind: "receipt"; receipt: MockExamSubmitReceipt }
  | { kind: "terminal_session"; session: MockExamLearnerSession };

interface MockExamLaunchContext {
  requestedSessionId: string;
  requestedPlanTaskId: string;
  returnUrl: string;
}

const runtimes = new WeakMap<MockExamPageInstance, PageRuntime>();
const launchContexts = new WeakMap<MockExamPageInstance, MockExamLaunchContext>();
const mockAnsweringPolicy = taskToolPolicy("mock", "answering");
const disputeCategories: MockExamPageData["disputeCategories"] = [
  { value: "stem_error", label: "题干错误" },
  { value: "option_error", label: "选项错误" },
  { value: "answer_error", label: "答案争议" },
  { value: "material_mismatch", label: "材料不匹配" },
  { value: "image_unreadable", label: "图片无法辨认" },
  { value: "other", label: "其他问题" },
];

Page<MockExamPageData, MockExamPageCustom>({
  data: {
    status: "loading",
    errorTitle: "",
    errorMessage: "",
    showLoginAction: false,
    title: "国考行测整卷模考",
    question: null,
    current: 1,
    total: 0,
    answered: 0,
    unanswered: 0,
    marked: 0,
    remainingLabel: "02:00:00",
    syncLabel: "正在连接",
    answerSheetItems: [],
    answerSheetOpen: false,
    draftOpen: false,
    materialOpen: false,
    toolsOpen: false,
    helpOpen: false,
    interactionLocked: false,
    fontScale: "standard",
    eyeCare: false,
    contentPreferences: { fontScale: 1, colorMode: "standard" },
    materialReaderState: createMaterialReaderState(),
    materialQuestionId: "",
    toolPolicy: mockAnsweringPolicy,
    pageObjectReference: null,
    helpStatus: "idle",
    helpMessage: "",
    disputeOpen: false,
    disputeCategories,
    disputeCategory: "stem_error",
    disputeDescription: "",
    disputeStatus: "idle",
    disputeMessage: "",
    disputeReceipt: null,
    receipt: null,
    reportSessionId: "",
    submitConfirmOpen: false,
    submitElapsedLabel: "",
    submitMissingLabels: [],
    questionTransition: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const contentPreferences = loadTaskDisplayPreferences();
    this.setData({
      contentPreferences,
      fontScale: contentPreferences.fontScale > 1 ? "large" : "standard",
      eyeCare: contentPreferences.colorMode === "eye_care",
      materialReaderState: createMaterialReaderState(undefined, contentPreferences),
    });
    const requestedSessionId = options.sessionId?.trim() ?? "";
    const requestedPlanTaskId = options.planTaskId?.trim() ?? "";
    if (
      (requestedSessionId && requestedPlanTaskId) ||
      (requestedSessionId && !isValidPlanTaskId(requestedSessionId)) ||
      (requestedPlanTaskId && !isValidPlanTaskId(requestedPlanTaskId))
    ) {
      this.setData({
        status: "unavailable",
        errorTitle: "无法确认本次模考任务",
        errorMessage: "请从今日计划或报告中心重新进入。",
      });
      return;
    }
    const context: MockExamLaunchContext = {
      requestedSessionId,
      requestedPlanTaskId,
      returnUrl: mockExamReturnUrl(requestedSessionId, requestedPlanTaskId),
    };
    launchContexts.set(this, context);
    this.setData({ reportSessionId: requestedSessionId });
    const auth = currentAuthSession();
    if (!auth) {
      showMockLoginRequired(this, false);
      return;
    }
    bindMockSession(this, context, auth, false);
    void this.loadExam();
  },

  onShow() {
    const context = launchContexts.get(this);
    if (!context) return;
    const auth = currentAuthSession();
    const currentRuntime = runtimes.get(this);
    if (!auth) {
      if (currentRuntime) enterMockAuthRequired(this, currentRuntime);
      else showMockLoginRequired(this, false);
      return;
    }
    const runtime =
      currentRuntime && sameSessionOwner(auth, currentRuntime.authSession)
        ? currentRuntime
        : bindMockSession(this, context, auth, true);
    if (runtime !== currentRuntime) {
      void this.loadExam();
      return;
    }
    if (runtime.disposed) return;
    runtime.visible = true;
    if (runtime.session && runtime.task && this.data.status === "ready") {
      refreshClock(this, runtime);
      startClock(this, runtime);
      queueTrajectory(runtime, "resumed", null, {
        currentQuestionId: currentQuestionId(runtime),
        remainingSeconds: remainingMockSeconds(runtime.session.deadlineAt, Date.now()),
      });
    }
  },

  onHide() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    runtime.visible = false;
    stopClock(runtime);
    clearAutosave(runtime);
    if (runtime.session && runtime.task && this.data.status === "ready") {
      queueTrajectory(runtime, "left", null, {
        currentQuestionId: currentQuestionId(runtime),
        answeredCount: countMockAnswers(runtime.task).answered,
      });
    }
    void saveCheckpoint(this, runtime);
  },

  onUnload() {
    const runtime = runtimes.get(this);
    if (runtime) disposeRuntime(runtime);
    runtimes.delete(this);
    launchContexts.delete(this);
  },

  async loadExam() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    this.setData({
      status: "loading",
      errorTitle: "",
      errorMessage: "",
      showLoginAction: false,
    });
    try {
      const session = runtime.requestedSessionId
        ? await runtime.api.getSession(runtime.requestedSessionId)
        : await runtime.api.createOrRestoreWeekly(
            await createIdempotencyKey("mock-start"),
            runtime.requestedPlanTaskId || undefined,
          );
      if (runtimes.get(this) !== runtime) return;
      if (runtime.requestedPlanTaskId && !session.planTaskId) {
        throw new Error("本场模考没有绑定今日任务，请返回今日计划后重新进入。");
      }
      if (session.status !== "assigned") {
        convergeToTerminalSession(this, runtime, session);
        return;
      }
      const pendingSubmitBeforeClaim = loadPendingMockSubmit(
        runtime.ownerUserId,
        session.sessionId,
      );
      const pendingCheckpointBeforeClaim = loadPendingMockCheckpoint(
        runtime.ownerUserId,
        session.sessionId,
      );
      const localBaseRevision =
        pendingCheckpointBeforeClaim?.command.expectedRevision ??
        pendingSubmitBeforeClaim?.command.expectedRevision ??
        session.revision;
      runtime.editOwnership = await acquireTaskEditOwnership(
        runtime.editingApi,
        "mock_exam_session",
        session.sessionId,
        localBaseRevision,
        pendingSubmitBeforeClaim !== undefined || pendingCheckpointBeforeClaim !== undefined,
      );
      if (runtimes.get(this) !== runtime) return;
      if (runtime.editOwnership.conflictChoice === "use_server") {
        clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
        clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
      } else if (runtime.editOwnership.conflictChoice === "use_this_device") {
        await rebasePendingMockAfterChoice(
          runtime,
          session,
          pendingCheckpointBeforeClaim,
          pendingSubmitBeforeClaim,
        );
      }
      if (runtimes.get(this) !== runtime) return;
      runtime.session = session;
      runtime.task = runtimeFromMockSession(session, Date.now());
      initializeTrajectory(runtime, session);
      applyRuntimeToPage(this, runtime, "已恢复上次进度");
      queueTrajectory(runtime, "visited", currentQuestionId(runtime), { entry: "session_load" });

      const pendingSubmit = loadPendingMockSubmit(runtime.ownerUserId, session.sessionId);
      if (pendingSubmit) {
        await resumePendingSubmit(this, runtime, pendingSubmit);
        return;
      }
      const pendingCheckpoint = loadPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
      if (pendingCheckpoint) {
        runtime.task = runtimeWithPendingMockCheckpoint(runtime.task, pendingCheckpoint.command);
        runtime.dirtyQuestionIds = new Set(
          pendingCheckpoint.command.answers.map((answer) => answer.questionId),
        );
        runtime.mutationVersion += 1;
        applyRuntimeToPage(this, runtime, "正在确认上次进度");
        void resumePendingCheckpoint(this, runtime, pendingCheckpoint);
      }
      refreshClock(this, runtime);
      startClock(this, runtime);
    } catch (error) {
      if (runtimes.get(this) !== runtime) return;
      if (enterMockAuthRequired(this, runtime, error)) return;
      if (error instanceof TaskEditTakeoverDeclined) {
        this.setData({
          status: "unavailable",
          errorTitle: "此设备没有编辑权",
          errorMessage: "模考仍由另一台设备作答。计时不会暂停；需要继续时请重新进入并确认接管。",
        });
        return;
      }
      failPage(this, error, "整卷恢复失败");
    }
  },

  retry() {
    const context = launchContexts.get(this);
    const auth = currentAuthSession();
    if (!context || !auth) {
      const runtime = runtimes.get(this);
      if (runtime) enterMockAuthRequired(this, runtime);
      else showMockLoginRequired(this, false);
      return;
    }
    const currentRuntime = runtimes.get(this);
    if (!currentRuntime || !sameSessionOwner(auth, currentRuntime.authSession)) {
      bindMockSession(this, context, auth, true);
    }
    void this.loadExam();
  },

  goLogin() {
    const returnUrl = launchContexts.get(this)?.returnUrl;
    void wx.reLaunch({
      url: returnUrl ? loginUrlForReturn(returnUrl) : routes.login,
    });
  },

  async exit() {
    const runtime = runtimes.get(this);
    if (!runtime?.task) {
      void wx.navigateBack();
      return;
    }
    const saved = await saveCheckpoint(this, runtime);
    if (!saved) return;
    const result = await wx.showModal({
      title: "模考计时仍会继续",
      content: "离开页面不会暂停计时；回来后会按统一结束时间恢复。",
      confirmText: "仍要离开",
      cancelText: "继续作答",
    });
    if (result.confirm) void wx.navigateBack();
  },

  selectOption(event) {
    const optionId = dataset(event, "optionid");
    mutateTask(this, { type: "ANSWER_SELECTED", optionIds: [optionId] });
    const runtime = requiredRuntime(this);
    queueTrajectory(runtime, "answer_changed", currentQuestionId(runtime), {
      selectedOptionId: optionId,
    });
  },

  toggleEliminated(event) {
    const runtime = requiredRuntime(this);
    const optionId = dataset(event, "optionid");
    const current = currentAttempt(runtime);
    mutateTask(this, {
      type: "OPTION_ELIMINATED",
      optionId,
      eliminated: !current.eliminatedOptionIds.includes(optionId),
    });
    queueTrajectory(runtime, "answer_changed", currentQuestionId(runtime), {
      interaction: "option_elimination",
      optionId,
    });
  },

  toggleMarked() {
    const runtime = requiredRuntime(this);
    const marked = !currentAttempt(runtime).marked;
    mutateTask(this, { type: "QUESTION_MARKED", marked });
    queueTrajectory(runtime, "mark_changed", currentQuestionId(runtime), { marked });
  },

  inputDraft(event) {
    mutateTask(this, { type: "DRAFT_CHANGED", text: event.detail.value });
  },

  previousQuestion() {
    moveQuestion(this, -1);
  },

  nextQuestion() {
    moveQuestion(this, 1);
  },

  // 左右快滑切题（与 pages/practice 同一手势语义）。判定纯函数见 task-runtime/swipe-nav.ts；
  // 高阈值 + 横向占优 + 速度判定用于避开纵向滚动与 content-renderer 表格的横向滚动。
  onQuestionTouchStart(event) {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    runtime.swipeStart = swipeSampleFromTouchStart(event);
  },

  onQuestionTouchMove(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.swipeStart) return;
    // 第二根手指落下（表格缩放/双指滚动）即放弃本次切题手势。
    if (event.touches.length > 1) runtime.swipeStart = null;
  },

  onQuestionTouchEnd(event) {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    const start = runtime.swipeStart;
    runtime.swipeStart = null;
    if (!start || swipeNavigationBlocked(this)) return;
    const direction = resolveSwipeNav(start, swipeSampleFromTouchEnd(event));
    if (!direction) return;
    if (direction === "prev") {
      if (this.data.current <= 1) {
        void wx.showToast({ title: "已经是第一题", icon: "none", duration: 1200 });
        return;
      }
      this.setData({ questionTransition: "prev" });
      this.previousQuestion();
      return;
    }
    // 末题的底部主按钮语义是交卷：快滑永远不代点交卷，只轻提示。
    if (this.data.current >= this.data.total) {
      void wx.showToast({ title: "已经是最后一题", icon: "none", duration: 1200 });
      return;
    }
    this.setData({ questionTransition: "next" });
    this.nextQuestion();
  },

  clearQuestionTransition() {
    if (this.data.questionTransition) this.setData({ questionTransition: "" });
  },

  selectQuestion(event) {
    const runtime = requiredRuntime(this);
    const index = Number(dataset(event, "index"));
    const previousQuestionId = currentQuestionId(runtime);
    const previousIndex = requiredTask(runtime).currentQuestionIndex;
    const skipped = currentAttempt(runtime).selectedOptionIds.length === 0;
    markCurrentQuestionDirty(runtime);
    runtime.task = reduceTaskRuntime(requiredTask(runtime), { type: "QUESTION_CHANGED", index });
    runtime.mutationVersion += 1;
    // 交卷确认面板中点未答题号 = 回去检查该题（Figma 98:192）。
    this.setData({ answerSheetOpen: false, submitConfirmOpen: false });
    applyRuntimeToPage(this, runtime, "待保存");
    if (requiredTask(runtime).currentQuestionIndex !== previousIndex) resetPageScroll();
    scheduleAutosave(this, runtime);
    if (skipped) {
      queueTrajectory(runtime, "skipped", previousQuestionId, { destinationIndex: index });
    }
    queueTrajectory(runtime, "visited", currentQuestionId(runtime), { source: "answer_sheet" });
  },

  toggleAnswerSheet() {
    this.setData({
      answerSheetOpen: !this.data.answerSheetOpen,
      draftOpen: false,
      toolsOpen: false,
      helpOpen: false,
    });
  },

  toggleDraft() {
    if (this.data.status !== "ready" || !this.data.question) return;
    this.setData({
      draftOpen: !this.data.draftOpen,
      answerSheetOpen: false,
      toolsOpen: false,
      helpOpen: false,
    });
  },

  toggleMaterial() {
    if (!this.data.question?.material) return;
    const opening = !this.data.materialOpen;
    const materialReaderState = reduceMaterialReader(
      this.data.materialReaderState,
      this.data.materialOpen ? { type: "RETURN_TO_QUESTION" } : { type: "OPEN_DRAWER" },
    );
    this.setData({
      materialOpen: materialReaderState.mode !== "question_first",
      materialReaderState,
    });
    // 契约增量 v1：展开材料是可见的作答动作事实（与真实训练同套采集）。
    const runtime = runtimes.get(this);
    if (opening && runtime?.task && runtime.task.phase === "answering") {
      runtime.task = reduceTaskRuntime(runtime.task, {
        type: "ACTION_LOGGED",
        action: "material_expanded",
      });
    }
  },

  updateMaterialReaderState(event) {
    const preferencesChanged =
      event.detail.state.preferences.fontScale !== this.data.contentPreferences.fontScale ||
      event.detail.state.preferences.colorMode !== this.data.contentPreferences.colorMode;
    this.setData({
      materialReaderState: event.detail.state,
      materialOpen: event.detail.state.mode !== "question_first",
      contentPreferences: event.detail.state.preferences,
      fontScale: event.detail.state.preferences.fontScale > 1 ? "large" : "standard",
      eyeCare: event.detail.state.preferences.colorMode === "eye_care",
    });
    if (preferencesChanged) storeTaskDisplayPreferences(event.detail.state.preferences);
  },

  updateMaterialPosition(event) {
    const runtime = requiredRuntime(this);
    if (this.data.interactionLocked) return;
    markCurrentQuestionDirty(runtime);
    runtime.task = reduceTaskRuntime(requiredTask(runtime), {
      type: "MATERIAL_POSITION_CHANGED",
      position: event.detail.position,
    });
    runtime.mutationVersion += 1;
    this.setData({ syncLabel: "待保存" });
    scheduleAutosave(this, runtime);
  },

  toggleTools() {
    this.setData({
      toolsOpen: !this.data.toolsOpen,
      answerSheetOpen: false,
      draftOpen: false,
      helpOpen: false,
    });
  },

  openFocusedTool() {
    this.openHelp();
  },

  openHelp() {
    this.setData({ helpOpen: true, toolsOpen: false, answerSheetOpen: false, draftOpen: false });
  },

  closeHelp() {
    this.setData({ helpOpen: false });
  },

  async submitHelp(event) {
    const runtime = requiredRuntime(this);
    this.setData({ helpStatus: "submitting", helpMessage: "正在提交操作环境信息……" });
    try {
      const response = await runtime.helpApi.submit(
        event.detail,
        await createIdempotencyKey("task-help"),
      );
      if (runtime.disposed) return;
      this.setData({
        helpStatus: "resolved",
        helpMessage: `${response.data.title}：${response.data.steps.join("；")}`,
      });
    } catch (error) {
      if (runtime.disposed) return;
      if (enterMockAuthRequired(this, runtime, error)) return;
      this.setData({
        helpStatus: "error",
        helpMessage: errorMessage(error, "帮助请求未提交成功，请保留当前页面后重试。"),
      });
    }
  },

  setFontScale(event) {
    const fontScale = dataset(event, "scale");
    if (fontScale !== "standard" && fontScale !== "large") return;
    const contentPreferences: ContentDisplayPreferences = {
      ...this.data.contentPreferences,
      fontScale: fontScale === "large" ? 1.2 : 1,
    };
    this.setData({
      fontScale,
      contentPreferences,
      materialReaderState: { ...this.data.materialReaderState, preferences: contentPreferences },
    });
    storeTaskDisplayPreferences(contentPreferences);
  },

  toggleEyeCare() {
    const eyeCare = !this.data.eyeCare;
    const contentPreferences: ContentDisplayPreferences = {
      ...this.data.contentPreferences,
      colorMode: eyeCare ? "eye_care" : "standard",
    };
    this.setData({
      eyeCare,
      contentPreferences,
      materialReaderState: { ...this.data.materialReaderState, preferences: contentPreferences },
    });
    storeTaskDisplayPreferences(contentPreferences);
  },

  reportDispute() {
    this.setData({
      disputeOpen: true,
      toolsOpen: false,
      helpOpen: false,
      disputeStatus: "idle",
      disputeMessage: "",
      disputeReceipt: null,
    });
  },

  closeDispute() {
    if (this.data.disputeStatus === "submitting") return;
    this.setData({ disputeOpen: false });
  },

  selectDisputeCategory(event) {
    const category = dataset(event, "category");
    if (!disputeCategories.some((item) => item.value === category)) return;
    this.setData({ disputeCategory: category as MockExamDisputeCategory });
  },

  inputDisputeDescription(event) {
    this.setData({ disputeDescription: event.detail.value });
  },

  async submitDispute() {
    const runtime = requiredRuntime(this);
    const question = this.data.question;
    const description = this.data.disputeDescription.trim();
    if (!question || description.length < 2) {
      this.setData({ disputeStatus: "error", disputeMessage: "请用至少两个字说明争议。" });
      return;
    }
    this.setData({ disputeStatus: "submitting", disputeMessage: "正在提交这道题的问题……" });
    try {
      const receipt = await runtime.api.reportDispute(
        requiredSession(runtime).sessionId,
        {
          questionId: question.questionId,
          contentVersionId: question.contentVersionId,
          category: this.data.disputeCategory,
          description,
        },
        await createIdempotencyKey("mock-dispute"),
      );
      if (runtimes.get(this) !== runtime) return;
      this.setData({
        disputeStatus: "resolved",
        disputeMessage: "已收到，我们会按本场题目内容核查。",
        disputeReceipt: receipt,
      });
    } catch (error) {
      if (runtimes.get(this) !== runtime) return;
      if (enterMockAuthRequired(this, runtime, error)) return;
      this.setData({
        disputeStatus: "error",
        disputeMessage: errorMessage(error, "争议没有提交成功，请保留当前页面后重试。"),
      });
    }
  },

  // Figma 98:2：底部确认面板取代系统弹窗，展示真实的已答/未答/标记与未答题号。
  submitExam() {
    if (this.data.status !== "ready" || this.data.interactionLocked) return;
    const runtime = requiredRuntime(this);
    const task = requiredTask(runtime);
    const missingLabels = task.questionOrder
      .map((questionId, index) => ({
        label: String(index + 1),
        answered: (task.attempts[questionId]?.selectedOptionIds.length ?? 0) > 0,
      }))
      .filter((item) => !item.answered)
      .map((item) => item.label);
    this.setData({
      submitConfirmOpen: true,
      submitElapsedLabel: formatRemainingTime(task.totalElapsedSeconds),
      submitMissingLabels: missingLabels,
      answerSheetOpen: false,
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
    });
  },

  closeSubmitConfirm() {
    this.setData({ submitConfirmOpen: false });
  },

  async confirmSubmitExam() {
    if (this.data.status !== "ready" || this.data.interactionLocked) {
      this.setData({ submitConfirmOpen: false });
      return;
    }
    const runtime = requiredRuntime(this);
    this.setData({ submitConfirmOpen: false });
    await executeSubmit(this, runtime);
  },

  returnToday() {
    void wx.switchTab({ url: "/pages/today/index" });
  },

  openMockReport() {
    const sessionId = this.data.reportSessionId.trim();
    if (!sessionId) {
      this.setData({
        errorMessage: "缺少本场模考会话编号，不能打开正式报告。请从报告中心或今日计划重新进入。",
      });
      return;
    }
    void wx.navigateTo({ url: `${routes.mockReport}?sessionId=${encodeURIComponent(sessionId)}` });
  },

  noop() {},
});

function mutateTask(
  page: MockExamPageInstance,
  event: Parameters<typeof reduceTaskRuntime>[1],
): void {
  const runtime = requiredRuntime(page);
  if (page.data.interactionLocked) return;
  markCurrentQuestionDirty(runtime);
  runtime.task = reduceTaskRuntime(requiredTask(runtime), event);
  runtime.mutationVersion += 1;
  applyRuntimeToPage(page, runtime, "待保存");
  scheduleAutosave(page, runtime);
}

/**
 * 快滑切题的守卫矩阵（模考面板集）：任一 sheet/面板、材料沉浸态或
 * 交互锁定激活时手势一律不触发（宁可不触发不可误触发）。
 */
function swipeNavigationBlocked(page: MockExamPageInstance): boolean {
  const data = page.data;
  return (
    data.status !== "ready" ||
    data.question === null ||
    data.interactionLocked ||
    data.materialOpen ||
    data.answerSheetOpen ||
    data.draftOpen ||
    data.toolsOpen ||
    data.helpOpen ||
    data.disputeOpen ||
    data.submitConfirmOpen
  );
}

function moveQuestion(page: MockExamPageInstance, delta: number): void {
  const runtime = requiredRuntime(page);
  const task = requiredTask(runtime);
  const index = task.currentQuestionIndex + delta;
  if (index < 0 || index >= task.questionOrder.length) return;
  const previousQuestionId = currentQuestionId(runtime);
  const skipped = currentAttempt(runtime).selectedOptionIds.length === 0;
  markCurrentQuestionDirty(runtime);
  runtime.task = reduceTaskRuntime(task, { type: "QUESTION_CHANGED", index });
  runtime.mutationVersion += 1;
  applyRuntimeToPage(page, runtime, "待保存");
  resetPageScroll();
  scheduleAutosave(page, runtime);
  if (skipped) queueTrajectory(runtime, "skipped", previousQuestionId, { direction: delta });
  queueTrajectory(runtime, "visited", currentQuestionId(runtime), { direction: delta });
}

function resetPageScroll(): void {
  // 模考题面走页面级滚动，切题后立即回顶，避免带着上一题的滚动位置看新题。
  if (typeof wx.pageScrollTo !== "function") return;
  void wx.pageScrollTo({ scrollTop: 0, duration: 0 });
}

function applyRuntimeToPage(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  syncLabel: string,
): void {
  const session = requiredSession(runtime);
  const task = requiredTask(runtime);
  const question = session.questions[task.currentQuestionIndex];
  if (!question) throw new Error("Current mock question is unavailable");
  const attempt = currentAttempt(runtime);
  const material = question.materialId
    ? (session.materials.find((item) => item.materialId === question.materialId) ?? null)
    : null;
  if (question.materialId && !material) throw new Error("Current mock material is unavailable");
  const counts = countMockAnswers(task);
  const materialReaderState =
    page.data.materialQuestionId === question.questionId
      ? {
          ...page.data.materialReaderState,
          ...(attempt.materialPosition ? { position: { ...attempt.materialPosition } } : {}),
          preferences: page.data.contentPreferences,
        }
      : createMaterialReaderState(
          attempt.materialPosition ?? {
            anchorId: material?.anchors.at(0)?.anchorId ?? "material-start",
            scrollTop: 0,
            zoom: 1,
          },
          page.data.contentPreferences,
        );
  const answerSheetItems = task.questionOrder.map((questionId, index): MockAnswerSheetItem => {
    const item = task.attempts[questionId];
    if (!item) throw new Error("Mock answer sheet is incomplete");
    return {
      index,
      label: String(index + 1),
      state: item.marked ? "marked" : item.selectedOptionIds.length > 0 ? "answered" : "unanswered",
      active: index === task.currentQuestionIndex,
    };
  });
  page.setData({
    status: "ready",
    showLoginAction: false,
    interactionLocked: false,
    title: session.title,
    reportSessionId: session.sessionId,
    question: {
      ...question,
      options: question.options.map((option) => ({
        ...option,
        selected: attempt.selectedOptionIds.includes(option.optionId),
        eliminated: attempt.eliminatedOptionIds.includes(option.optionId),
      })),
      marked: attempt.marked,
      draftText: attempt.draftText,
      material,
    },
    current: task.currentQuestionIndex + 1,
    total: task.questionOrder.length,
    answered: counts.answered,
    unanswered: counts.unanswered,
    marked: counts.marked,
    syncLabel,
    answerSheetItems,
    materialReaderState,
    materialQuestionId: question.questionId,
    materialOpen:
      page.data.materialQuestionId === question.questionId && Boolean(material)
        ? page.data.materialOpen
        : false,
    pageObjectReference: {
      pageCode: "F02",
      route: "/pages/mock-exam/index",
      track: "xingce",
      objectType: "task_session",
      objectId: session.sessionId,
      taskMode: "mock",
      lifecycle: "answering",
    },
  });
}

function scheduleAutosave(page: MockExamPageInstance, runtime: PageRuntime): void {
  clearAutosave(runtime);
  runtime.autosaveTimerId = setTimeout(() => {
    delete runtime.autosaveTimerId;
    void saveCheckpoint(page, runtime);
  }, 800);
}

function clearAutosave(runtime: PageRuntime): void {
  if (runtime.autosaveTimerId === undefined) return;
  clearTimeout(runtime.autosaveTimerId);
  delete runtime.autosaveTimerId;
}

async function saveCheckpoint(page: MockExamPageInstance, runtime: PageRuntime): Promise<boolean> {
  if (runtime.savePromise) return runtime.savePromise;
  const task = runtime.task;
  const session = runtime.session;
  if (!task || !session || task.syncState === "saved") return true;
  const operation = performCheckpointSave(page, runtime, task, session);
  runtime.savePromise = operation;
  try {
    return await operation;
  } finally {
    delete runtime.savePromise;
  }
}

async function performCheckpointSave(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  task: TaskRuntimeState,
  session: MockExamLearnerSession,
): Promise<boolean> {
  const idempotencyKey = await createIdempotencyKey("mock-checkpoint");
  if (runtime.disposed) return false;
  const sentQuestionIds = new Set(runtime.dirtyQuestionIds);
  const command = buildMockCheckpointCommand(task, sentQuestionIds);
  const sentMutationVersion = runtime.mutationVersion;
  const pending: PendingMockCheckpoint = {
    kind: "checkpoint",
    ownerUserId: runtime.ownerUserId,
    sessionId: session.sessionId,
    idempotencyKey,
    command,
    storedAt: new Date().toISOString(),
  };
  storePendingMockCheckpoint(pending);
  runtime.task = reduceTaskRuntime(requiredTask(runtime), { type: "SAVE_REQUESTED" });
  page.setData({ syncLabel: "正在保存" });
  try {
    const receipt = await runtime.api.saveCheckpoint(
      session.sessionId,
      command,
      idempotencyKey,
      requiredMockEditToken(runtime),
    );
    if (runtimes.get(page) !== runtime) return true;
    clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
    const updated = sessionWithCheckpointReceipt(session, receipt);
    runtime.session = updated;
    runtime.task =
      runtime.mutationVersion === sentMutationVersion
        ? // 契约增量 v1：带上本地状态重建，毫秒时钟与事件时间线不倒退。
          runtimeFromMockSession(updated, Date.now(), requiredTask(runtime))
        : runtimeWithServerRevision(requiredTask(runtime), updated);
    if (runtime.mutationVersion === sentMutationVersion) {
      sentQuestionIds.forEach((questionId) => runtime.dirtyQuestionIds.delete(questionId));
    }
    applyRuntimeToPage(
      page,
      runtime,
      runtime.mutationVersion === sentMutationVersion ? "已保存" : "有新修改待保存",
    );
    if (runtime.mutationVersion !== sentMutationVersion) scheduleAutosave(page, runtime);
    return true;
  } catch (error) {
    if (runtimes.get(page) !== runtime) return false;
    if (enterMockAuthRequired(page, runtime, error)) return false;
    if (
      error instanceof ApiClientError &&
      error.statusCode === 409 &&
      error.payload?.code === "task_edit_owned_elsewhere"
    ) {
      delete runtime.editOwnership;
      stopClock(runtime);
      runtime.task = reduceTaskRuntime(requiredTask(runtime), {
        type: "SAVE_FAILED",
        requestId: error.requestId,
      });
      page.setData({
        interactionLocked: true,
        syncLabel: "另一台设备已接管",
        errorMessage: "本机草稿仍保留；重新进入并确认接管后，系统会让你选择本机或云端进度。",
      });
      return false;
    }
    if (
      error instanceof ApiClientError &&
      error.statusCode === 409 &&
      error.payload?.code === "mock_revision_conflict"
    ) {
      return resolveMockCheckpointConflict(page, runtime, task, command.expectedRevision);
    }
    runtime.task = reduceTaskRuntime(requiredTask(runtime), {
      type: "SAVE_FAILED",
      requestId: error instanceof ApiClientError ? error.requestId : "mock-checkpoint-failed",
    });
    page.setData({
      syncLabel: "保存失败",
      errorMessage: errorMessage(error, "进度没有保存成功，请重试后再离开。"),
    });
    return false;
  }
}

async function resumePendingCheckpoint(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  pending: PendingMockCheckpoint,
): Promise<void> {
  if (runtime.savePromise) return;
  const operation = (async () => {
    try {
      const receipt = await runtime.api.saveCheckpoint(
        pending.sessionId,
        pending.command,
        pending.idempotencyKey,
        requiredMockEditToken(runtime),
      );
      if (runtimes.get(page) !== runtime) return true;
      clearPendingMockCheckpoint(runtime.ownerUserId, pending.sessionId);
      const updated = sessionWithCheckpointReceipt(requiredSession(runtime), receipt);
      runtime.session = updated;
      runtime.task = runtimeFromMockSession(updated, Date.now(), runtime.task);
      pending.command.answers.forEach((answer) => {
        runtime.dirtyQuestionIds.delete(answer.questionId);
      });
      applyRuntimeToPage(page, runtime, "已确认上次进度");
      return true;
    } catch (error) {
      if (runtimes.get(page) !== runtime) return false;
      if (enterMockAuthRequired(page, runtime, error)) return false;
      page.setData({
        syncLabel: "上次进度待确认",
        errorMessage: errorMessage(error, "上次保存结果未知，将继续确认刚才那次保存。"),
      });
      return false;
    }
  })();
  runtime.savePromise = operation;
  try {
    await operation;
  } finally {
    delete runtime.savePromise;
  }
}

async function executeSubmit(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  pending?: PendingMockSubmit,
  skipCheckpoint = false,
): Promise<void> {
  if (
    runtime.submitInFlight ||
    !ownsRuntime(runtime) ||
    page.data.status !== "ready" ||
    runtime.session?.status !== "assigned" ||
    !runtime.task
  ) {
    return;
  }
  runtime.submitInFlight = true;
  page.setData({ interactionLocked: true, syncLabel: "正在交卷" });
  try {
    if (skipCheckpoint) {
      clearAutosave(runtime);
      const inFlightSave = runtime.savePromise;
      if (inFlightSave) await inFlightSave;
      clearAutosave(runtime);
    } else if (!pending) {
      const saved = await saveCheckpoint(page, runtime);
      if (runtimes.get(page) !== runtime) return;
      if (!saved) {
        page.setData({ interactionLocked: false });
        return;
      }
    }
    if (runtimes.get(page) !== runtime) return;
    if (!(await flushTrajectory(runtime))) {
      throw new Error("作答过程还没确认成功，请保持网络后重试交卷。");
    }
    if (pending)
      runtime.task = runtimeWithPendingMockSubmit(requiredTask(runtime), pending.command);
    const session = requiredSession(runtime);
    const idempotencyKey = pending?.idempotencyKey ?? (await createIdempotencyKey("mock-submit"));
    const command = pending?.command ?? buildMockSubmitCommand(requiredTask(runtime));
    const submission: PendingMockSubmit =
      pending ??
      ({
        kind: "submit",
        ownerUserId: runtime.ownerUserId,
        sessionId: session.sessionId,
        idempotencyKey,
        command,
        storedAt: new Date().toISOString(),
      } satisfies PendingMockSubmit);
    storePendingMockSubmit(submission);
    const outcome = await submitWithLease(runtime, submission);
    if (runtimes.get(page) !== runtime) return;
    if (outcome.kind === "terminal_session") {
      convergeToTerminalSession(page, runtime, outcome.session);
      return;
    }
    const receipt = outcome.receipt;
    const committedSession = requiredSession(runtime);
    const task = requiredTask(runtime);
    clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
    clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
    stopClock(runtime);
    runtime.task = reduceTaskRuntime(task, {
      type: "TASK_SUBMITTED",
      submittedAt: receipt.review.submittedAt,
    });
    page.setData({
      status: "submitted",
      interactionLocked: true,
      answerSheetOpen: false,
      toolsOpen: false,
      helpOpen: false,
      syncLabel: "已交卷",
      receipt,
      reportSessionId: committedSession.sessionId,
      toolPolicy: taskToolPolicy("mock", "submitted"),
      pageObjectReference: {
        pageCode: "F02",
        route: "/pages/mock-exam/index",
        track: "xingce",
        objectType: "task_session",
        objectId: committedSession.sessionId,
        taskMode: "mock",
        lifecycle: "submitted",
      },
    });
  } catch (error) {
    if (runtimes.get(page) !== runtime) return;
    if (enterMockAuthRequired(page, runtime, error)) return;
    const lostEditing =
      error instanceof ApiClientError &&
      error.statusCode === 409 &&
      error.payload?.code === "task_edit_owned_elsewhere";
    if (lostEditing) {
      delete runtime.editOwnership;
      stopClock(runtime);
    }
    page.setData({
      interactionLocked: lostEditing,
      syncLabel: lostEditing ? "另一台设备已接管" : "交卷结果待确认",
      errorMessage: lostEditing
        ? "本机交卷命令仍保留；重新进入并确认接管后再提交。"
        : errorMessage(error, "交卷结果未知，已保留同一命令供恢复时继续确认。"),
    });
  } finally {
    runtime.submitInFlight = false;
  }
}

async function submitWithLease(
  runtime: PageRuntime,
  submission: PendingMockSubmit,
): Promise<MockSubmitOutcome> {
  try {
    return {
      kind: "receipt",
      receipt: await runtime.api.submit(
        submission.sessionId,
        submission.command,
        submission.idempotencyKey,
        requiredMockEditToken(runtime),
      ),
    };
  } catch (error) {
    if (
      !(error instanceof ApiClientError) ||
      error.statusCode !== 409 ||
      error.payload?.code !== "mock_submission_conflict"
    ) {
      throw error;
    }
    const ownership = runtime.editOwnership;
    if (!ownership) throw error;
    const resolved = await resolveRevisionConflict(
      runtime.editingApi,
      ownership.lease,
      submission.command.expectedRevision,
    );
    if (!ownsRuntime(runtime)) {
      throw new Error("Mock runtime was invalidated during submit recovery", { cause: error });
    }
    runtime.editOwnership = resolved;
    const latest = await runtime.api.getSession(submission.sessionId);
    if (!ownsRuntime(runtime)) {
      throw new Error("Mock runtime was invalidated during submit recovery", { cause: error });
    }
    if (latest.sessionId !== submission.sessionId) {
      throw new Error("Mock submit recovery changed session ownership", { cause: error });
    }
    if (latest.status !== "assigned") {
      return { kind: "terminal_session", session: latest };
    }
    const serverTask = runtimeFromMockSession(latest, Date.now(), runtime.task);
    const selectedTask =
      resolved.conflictChoice === "use_this_device"
        ? runtimeWithPendingMockSubmit(serverTask, submission.command)
        : serverTask;
    runtime.session = latest;
    runtime.task = selectedTask;
    const rebased: PendingMockSubmit = {
      ...submission,
      idempotencyKey: await createIdempotencyKey("mock-submit-resolved"),
      command: buildMockSubmitCommand(selectedTask),
      storedAt: new Date().toISOString(),
    };
    clearPendingMockSubmit(runtime.ownerUserId, submission.sessionId);
    storePendingMockSubmit(rebased);
    return {
      kind: "receipt",
      receipt: await runtime.api.submit(
        rebased.sessionId,
        rebased.command,
        rebased.idempotencyKey,
        requiredMockEditToken(runtime),
      ),
    };
  }
}

async function resumePendingSubmit(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  pending: PendingMockSubmit,
): Promise<void> {
  await executeSubmit(page, runtime, pending);
}

function startClock(page: MockExamPageInstance, runtime: PageRuntime): void {
  stopClock(runtime);
  if (!runtime.visible || page.data.status !== "ready") return;
  // 契约增量 v1：模考启用与真实训练同套的毫秒时钟，供动作事件 atMs/sessionAtMs 使用。
  runtime.lastTickAtMs = Date.now();
  runtime.timerId = setInterval(() => {
    tickActiveClock(runtime);
    refreshClock(page, runtime);
  }, 1000);
}

function stopClock(runtime: PageRuntime): void {
  delete runtime.lastTickAtMs;
  if (runtime.timerId === undefined) return;
  clearInterval(runtime.timerId);
  delete runtime.timerId;
}

function tickActiveClock(runtime: PageRuntime): void {
  const task = runtime.task;
  if (!task || task.phase !== "answering" || runtime.disposed) return;
  const now = Date.now();
  // 定时器约 1 秒一跳；防御性夹在 [0, 60s]，避免时钟回拨或长阻塞造成异常跳变。
  const ms = Math.min(60_000, Math.max(0, Math.round(now - (runtime.lastTickAtMs ?? now))));
  runtime.lastTickAtMs = now;
  runtime.task = reduceTaskRuntime(task, { type: "TICK", seconds: 1, ms });
}

function refreshClock(page: MockExamPageInstance, runtime: PageRuntime): void {
  const session = runtime.session;
  if (!session || runtime.disposed) return;
  const remaining = remainingMockSeconds(session.deadlineAt, Date.now());
  page.setData({ remainingLabel: formatRemainingTime(remaining) });
  if (remaining === 0 && page.data.status === "ready" && !runtime.submitInFlight) {
    stopClock(runtime);
    void executeSubmit(page, runtime, undefined, true);
  }
}

function currentAttempt(runtime: PageRuntime) {
  const task = requiredTask(runtime);
  const questionId = task.questionOrder[task.currentQuestionIndex];
  const attempt = questionId ? task.attempts[questionId] : undefined;
  if (!attempt) throw new Error("Current mock attempt is unavailable");
  return attempt;
}

function currentQuestionId(runtime: PageRuntime): string {
  const task = requiredTask(runtime);
  const questionId = task.questionOrder[task.currentQuestionIndex];
  if (!questionId) throw new Error("Current mock question is unavailable");
  return questionId;
}

function initializeTrajectory(runtime: PageRuntime, session: MockExamLearnerSession): void {
  const pending = loadPendingMockTrajectory(runtime.ownerUserId, session.sessionId);
  runtime.nextTrajectoryEventSeq = Math.max(
    session.nextTrajectoryEventSeq,
    ...pending.map((item) => item.command.eventSeq + 1),
  );
  if (pending.length > 0) void flushTrajectory(runtime);
}

function queueTrajectory(
  runtime: PageRuntime,
  eventType: MockExamTrajectoryCommand["eventType"],
  questionId: string | null,
  payload: MockExamTrajectoryCommand["payload"],
): void {
  const session = runtime.session;
  if (!session || session.status !== "assigned" || runtime.disposed) return;
  const eventSeq = runtime.nextTrajectoryEventSeq;
  runtime.nextTrajectoryEventSeq += 1;
  const now = new Date().toISOString();
  const pending: PendingMockTrajectoryEvent = {
    ownerUserId: runtime.ownerUserId,
    sessionId: session.sessionId,
    idempotencyKey: `mock-trajectory:${session.sessionId}:${String(eventSeq)}`,
    command: {
      eventSeq,
      eventType,
      questionId,
      clientOccurredAt: now,
      sessionRevision: session.revision,
      payload,
    },
    storedAt: now,
  };
  appendPendingMockTrajectory(pending);
  void flushTrajectory(runtime);
}

function flushTrajectory(runtime: PageRuntime): Promise<boolean> {
  if (runtime.trajectoryPromise) return runtime.trajectoryPromise;
  const operation = performTrajectoryFlush(runtime);
  runtime.trajectoryPromise = operation;
  void operation.finally(() => {
    if (runtime.trajectoryPromise === operation) delete runtime.trajectoryPromise;
  });
  return operation;
}

async function performTrajectoryFlush(runtime: PageRuntime): Promise<boolean> {
  const session = runtime.session;
  if (!session) return true;
  let rebased = false;
  while (!runtime.disposed) {
    const pending = loadPendingMockTrajectory(runtime.ownerUserId, session.sessionId);
    const item = pending.at(0);
    if (!item) return true;
    try {
      await runtime.api.recordTrajectory(item.sessionId, item.command, item.idempotencyKey);
      if (!ownsRuntime(runtime)) return false;
      removePendingMockTrajectory(runtime.ownerUserId, item.sessionId, item.idempotencyKey);
    } catch (error) {
      if (enterMockAuthRequired(runtime.page, runtime, error)) return false;
      if (!(error instanceof ApiClientError) || error.statusCode !== 409 || rebased) return false;
      try {
        const latest = await runtime.api.getSession(session.sessionId);
        if (!ownsRuntime(runtime)) return false;
        if (latest.status !== "assigned") {
          replacePendingMockTrajectory(runtime.ownerUserId, session.sessionId, []);
          return true;
        }
        runtime.session = latest;
        rebaseTrajectory(runtime, latest.nextTrajectoryEventSeq);
        rebased = true;
      } catch (recoveryError) {
        if (enterMockAuthRequired(runtime.page, runtime, recoveryError)) return false;
        return false;
      }
    }
  }
  return false;
}

function rebaseTrajectory(runtime: PageRuntime, firstEventSeq: number): void {
  const session = requiredSession(runtime);
  const pending = loadPendingMockTrajectory(runtime.ownerUserId, session.sessionId).map(
    (item, index): PendingMockTrajectoryEvent => {
      const eventSeq = firstEventSeq + index;
      return {
        ...item,
        idempotencyKey: `mock-trajectory:${session.sessionId}:${String(eventSeq)}:rebase`,
        command: {
          ...item.command,
          eventSeq,
          sessionRevision: session.revision,
        },
      };
    },
  );
  replacePendingMockTrajectory(runtime.ownerUserId, session.sessionId, pending);
  runtime.nextTrajectoryEventSeq = firstEventSeq + pending.length;
}

async function rebasePendingMockAfterChoice(
  runtime: PageRuntime,
  session: MockExamLearnerSession,
  checkpoint: PendingMockCheckpoint | undefined,
  submission: PendingMockSubmit | undefined,
): Promise<void> {
  let localTask = runtimeFromMockSession(session, Date.now());
  const storedAt = new Date().toISOString();
  if (checkpoint) {
    localTask = runtimeWithPendingMockCheckpoint(localTask, checkpoint.command);
    const idempotencyKey = await createIdempotencyKey("mock-checkpoint-resolved");
    if (runtime.disposed) return;
    clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
    storePendingMockCheckpoint({
      ...checkpoint,
      idempotencyKey,
      command: buildMockCheckpointCommand(localTask),
      storedAt,
    });
  }
  if (submission) {
    localTask = runtimeWithPendingMockSubmit(localTask, submission.command);
    const idempotencyKey = await createIdempotencyKey("mock-submit-resolved");
    if (runtime.disposed) return;
    clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
    storePendingMockSubmit({
      ...submission,
      idempotencyKey,
      command: buildMockSubmitCommand(localTask),
      storedAt,
    });
  }
}

async function resolveMockCheckpointConflict(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  localTask: TaskRuntimeState,
  localBaseRevision: number,
): Promise<boolean> {
  const ownership = runtime.editOwnership;
  if (!ownership) throw new Error("当前设备编辑权没有初始化");
  try {
    const resolved = await resolveRevisionConflict(
      runtime.editingApi,
      ownership.lease,
      localBaseRevision,
    );
    if (runtimes.get(page) !== runtime) return false;
    runtime.editOwnership = resolved;
    const latest = await runtime.api.getSession(ownership.lease.resourceId);
    if (runtimes.get(page) !== runtime) return false;
    clearPendingMockCheckpoint(runtime.ownerUserId, ownership.lease.resourceId);
    if (latest.status !== "assigned") {
      convergeToTerminalSession(page, runtime, latest);
      return true;
    }
    runtime.session = latest;
    if (resolved.conflictChoice === "use_server") {
      runtime.task = runtimeFromMockSession(latest, Date.now(), runtime.task);
      runtime.dirtyQuestionIds.clear();
      applyRuntimeToPage(page, runtime, "已使用云端进度");
      return true;
    }
    const localCommand = buildMockCheckpointCommand(localTask);
    runtime.task = runtimeWithPendingMockCheckpoint(
      runtimeFromMockSession(latest, Date.now(), localTask),
      localCommand,
    );
    runtime.dirtyQuestionIds = new Set(runtime.task.questionOrder);
    runtime.mutationVersion += 1;
    const command = buildMockCheckpointCommand(runtime.task);
    const idempotencyKey = await createIdempotencyKey("mock-checkpoint-resolved");
    if (runtimes.get(page) !== runtime) return false;
    storePendingMockCheckpoint({
      kind: "checkpoint",
      ownerUserId: runtime.ownerUserId,
      sessionId: latest.sessionId,
      idempotencyKey,
      command,
      storedAt: new Date().toISOString(),
    });
    const receipt = await runtime.api.saveCheckpoint(
      latest.sessionId,
      command,
      idempotencyKey,
      requiredMockEditToken(runtime),
    );
    if (runtimes.get(page) !== runtime) return false;
    clearPendingMockCheckpoint(runtime.ownerUserId, latest.sessionId);
    const updated = sessionWithCheckpointReceipt(latest, receipt);
    runtime.session = updated;
    runtime.task = runtimeFromMockSession(updated, Date.now(), runtime.task);
    runtime.dirtyQuestionIds.clear();
    applyRuntimeToPage(page, runtime, "已按你的选择保留本机草稿");
    return true;
  } catch (error) {
    if (runtimes.get(page) !== runtime) return false;
    if (enterMockAuthRequired(page, runtime, error)) return false;
    runtime.task = reduceTaskRuntime(requiredTask(runtime), {
      type: "SAVE_FAILED",
      requestId: error instanceof ApiClientError ? error.requestId : "mock-conflict-failed",
    });
    page.setData({
      syncLabel: "冲突尚未解决",
      errorMessage: errorMessage(error, "你的选择没有确认成功，请保持当前页面后重试。"),
    });
    return false;
  }
}

function markCurrentQuestionDirty(runtime: PageRuntime): void {
  const task = requiredTask(runtime);
  const questionId = task.questionOrder[task.currentQuestionIndex];
  if (!questionId) throw new Error("Current mock question is unavailable");
  runtime.dirtyQuestionIds.add(questionId);
}

function requiredRuntime(page: MockExamPageInstance): PageRuntime {
  const runtime = runtimes.get(page);
  if (!runtime || runtime.disposed) throw new Error("Mock page runtime is unavailable");
  return runtime;
}

function requiredTask(runtime: PageRuntime): TaskRuntimeState {
  if (!runtime.task) throw new Error("Mock task has not loaded");
  return runtime.task;
}

function requiredSession(runtime: PageRuntime): MockExamLearnerSession {
  if (!runtime.session) throw new Error("Mock session has not loaded");
  return runtime.session;
}

function requiredMockEditToken(runtime: PageRuntime): { leaseId: string; generation: number } {
  const token = runtime.editOwnership?.token;
  if (!token) throw new Error("当前设备尚未取得模考编辑权");
  return token;
}

function sessionWithCheckpointReceipt(
  session: MockExamLearnerSession,
  receipt: MockExamCheckpointReceipt,
): MockExamLearnerSession {
  if (receipt.sessionId !== session.sessionId) {
    throw new Error("Mock checkpoint receipt changed session ownership");
  }
  if (receipt.revision <= session.revision) return session;
  const savedByQuestion = new Map(
    receipt.checkpoint.answers.map((answer) => [answer.questionId, answer]),
  );
  const previousByQuestion = new Map(
    session.checkpoint.answers.map((answer) => [answer.questionId, answer]),
  );
  return {
    ...session,
    status: receipt.status,
    revision: receipt.revision,
    checkpoint: {
      currentQuestionId: receipt.checkpoint.currentQuestionId,
      answers: session.questions.map((question) => {
        const answer =
          savedByQuestion.get(question.questionId) ?? previousByQuestion.get(question.questionId);
        if (!answer) throw new Error("Mock checkpoint receipt lost an assigned question");
        return answer;
      }),
    },
  };
}

function convergeToTerminalSession(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  session: MockExamLearnerSession,
): void {
  if (session.status === "assigned") {
    throw new Error("Mock terminal convergence requires a submitted or reviewed session");
  }
  const reviewed = session.status === "reviewed";
  runtime.session = session;
  delete runtime.task;
  delete runtime.editOwnership;
  runtime.dirtyQuestionIds.clear();
  stopClock(runtime);
  clearAutosave(runtime);
  clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
  clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
  page.setData({
    status: "submitted",
    title: session.title,
    errorTitle: reviewed ? "本场模考已完成" : "本场模考已经交卷",
    errorMessage: reviewed
      ? "服务端已确认交卷，正式模考报告可继续查看。"
      : "服务端已确认交卷，正式模考报告可进入查看。",
    showLoginAction: false,
    question: null,
    interactionLocked: true,
    syncLabel: reviewed ? "评阅已完成" : "交卷已确认",
    answerSheetOpen: false,
    draftOpen: false,
    materialOpen: false,
    toolsOpen: false,
    helpOpen: false,
    disputeOpen: false,
    submitConfirmOpen: false,
    questionTransition: "",
    receipt: null,
    reportSessionId: session.sessionId,
    toolPolicy: taskToolPolicy("mock", "submitted"),
    pageObjectReference: {
      pageCode: "F02",
      route: "/pages/mock-exam/index",
      track: "xingce",
      objectType: "task_session",
      objectId: session.sessionId,
      taskMode: "mock",
      lifecycle: "submitted",
    },
  });
}

function dataset(event: WechatMiniprogram.BaseEvent, name: string): string {
  const value: unknown = event.currentTarget.dataset[name];
  if (typeof value !== "string" && typeof value !== "number") {
    throw new Error(`Mock event requires ${name}`);
  }
  return String(value);
}

function failPage(page: MockExamPageInstance, error: unknown, title: string): void {
  page.setData({
    status: "error",
    errorTitle: title,
    errorMessage: errorMessage(error, "没有找到可继续作答的整卷。"),
  });
}

function bindMockSession(
  page: MockExamPageInstance,
  context: MockExamLaunchContext,
  authSession: ActiveAuthSession,
  visible: boolean,
): PageRuntime {
  const previous = runtimes.get(page);
  if (previous) {
    persistAuthRecoveryCheckpoint(previous);
    disposeRuntime(previous);
  }
  const client = new ApiClient(environment.apiBaseUrl, authSession.accessToken);
  const runtime: PageRuntime = {
    page,
    authSession: { ...authSession },
    ownerUserId: authSession.userId,
    api: new MockExamApi(client),
    helpApi: new TaskHelpApi(client),
    editingApi: new TaskEditingApi(client),
    requestedSessionId: context.requestedSessionId,
    requestedPlanTaskId: context.requestedPlanTaskId,
    visible,
    disposed: false,
    mutationVersion: 0,
    dirtyQuestionIds: new Set<string>(),
    nextTrajectoryEventSeq: 1,
    submitInFlight: false,
    swipeStart: null,
  };
  runtimes.set(page, runtime);
  page.setData({
    showLoginAction: false,
    interactionLocked: false,
    question: null,
    answerSheetOpen: false,
    materialOpen: false,
    toolsOpen: false,
    helpOpen: false,
    disputeOpen: false,
  });
  return runtime;
}

function enterMockAuthRequired(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  error?: unknown,
): boolean {
  const apiError = error instanceof ApiClientError ? error : null;
  if (error !== undefined && apiError?.statusCode !== 401) return false;
  if (runtimes.get(page) !== runtime) return true;
  const wasVisible = runtime.visible;
  persistAuthRecoveryCheckpoint(runtime);
  disposeRuntime(runtime);
  runtimes.delete(page);
  const replacement = currentAuthSession();
  const context = launchContexts.get(page);
  if (replacement && context && !sameSessionOwner(replacement, runtime.authSession)) {
    bindMockSession(page, context, replacement, wasVisible);
    void page.loadExam();
    return true;
  }
  showMockLoginRequired(page, apiError !== null, apiError?.requestId);
  return true;
}

function showMockLoginRequired(
  page: MockExamPageInstance,
  expired: boolean,
  requestId?: string,
): void {
  page.setData({
    status: "unavailable",
    errorTitle: expired ? "登录状态已过期" : "登录后才能开始整卷模考",
    errorMessage: expired
      ? `本机作答进度已保留，请重新登录后继续。${requestId ? `（请求 ${requestId}）` : ""}`
      : "模考时间、作答进度和交卷记录必须绑定你的账号。",
    showLoginAction: true,
    question: null,
    interactionLocked: true,
    syncLabel: expired ? "等待重新登录" : "尚未登录",
    answerSheetOpen: false,
    materialOpen: false,
    toolsOpen: false,
    helpOpen: false,
    disputeOpen: false,
    submitConfirmOpen: false,
  });
}

function persistAuthRecoveryCheckpoint(runtime: PageRuntime): void {
  const session = runtime.session;
  const task = runtime.task;
  if (!session || session.status !== "assigned" || !task) return;
  if (loadPendingMockSubmit(runtime.ownerUserId, session.sessionId)) return;
  if (task.syncState === "saved" && runtime.dirtyQuestionIds.size === 0) return;
  const existing = loadPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
  if (existing) {
    const includedQuestionIds = new Set(
      existing.command.answers.map((answer) => answer.questionId),
    );
    const currentForExistingScope = buildMockCheckpointCommand(task, includedQuestionIds);
    if (stableFingerprint(existing.command) === stableFingerprint(currentForExistingScope)) return;
  }
  const command = buildMockCheckpointCommand(task);
  const fingerprint = stableFingerprint({
    ownerUserId: runtime.ownerUserId,
    sessionId: session.sessionId,
    mutationVersion: runtime.mutationVersion,
    command,
  }).slice(0, 32);
  storePendingMockCheckpoint({
    kind: "checkpoint",
    ownerUserId: runtime.ownerUserId,
    sessionId: session.sessionId,
    idempotencyKey: `mock-auth-recovery-${fingerprint}`,
    command,
    storedAt: new Date().toISOString(),
  });
}

function disposeRuntime(runtime: PageRuntime): void {
  runtime.disposed = true;
  runtime.visible = false;
  runtime.swipeStart = null;
  stopClock(runtime);
  clearAutosave(runtime);
}

function ownsRuntime(runtime: PageRuntime): boolean {
  return !runtime.disposed && runtimes.get(runtime.page) === runtime;
}

function mockExamReturnUrl(requestedSessionId: string, requestedPlanTaskId: string): string {
  if (requestedSessionId) {
    return `${routes.mockExam}?sessionId=${encodeURIComponent(requestedSessionId)}`;
  }
  if (requestedPlanTaskId) {
    return `${routes.mockExam}?planTaskId=${encodeURIComponent(requestedPlanTaskId)}`;
  }
  return routes.mockExam;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}
