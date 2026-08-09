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
} from "./shared/api/mock-exam";
import { TaskEditingApi } from "./shared/api/task-editing";
import type { TaskEditOwnership } from "./shared/features/task-editing/coordinator";
import {
  acquireTaskEditOwnership,
  resolveRevisionConflict,
  TaskEditTakeoverDeclined,
} from "./shared/features/task-editing/coordinator";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { routes } from "../../app/navigation/routes";
import { isValidPlanTaskId } from "../../features/xingce/real_attempt/contracts";
import { TaskHelpApi, type TaskHelpCommand } from "./shared/api/task-help";
import { currentAuthSession, sameSessionOwner, type ActiveAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { createIdempotencyKey, stableFingerprint } from "../../platform/command-id";
import type { TaskRuntimeState } from "../../task-runtime/contracts";
import type { AnnotationStroke } from "../../task-runtime/contracts";
import {
  replaceMaterialAnnotations,
  replaceQuestionAnnotations,
  splitAnnotationSurfaces,
} from "./shared/task-runtime/annotation-layer/strokes";
import type { AnchorSegmentRect } from "./shared/task-runtime/annotation-layer/anchor";
import type { ContentDisplayPreferences } from "./shared/task-runtime/content-renderer/model";
import { stripOptionContentPrefix } from "./shared/task-runtime/option-presentation";
import {
  loadTaskDisplayPreferences,
  storeTaskDisplayPreferences,
} from "./shared/task-runtime/display-preferences";
import {
  createMaterialReaderState,
  reduceMaterialReader,
  type MaterialReaderPosition,
  type MaterialReaderState,
} from "./shared/task-runtime/material-reader/state";
import { reduceTaskRuntime } from "./shared/task-runtime/state/runtime";
import {
  createTaskInteractionState,
  projectTaskInteraction,
  reduceTaskInteraction,
  type TaskInteractionAction,
  type TaskInteractionProjection,
  type TaskInteractionState,
} from "./shared/task-runtime/interaction/state";
import {
  beginSwipeGesture,
  finishSwipeGesture,
  idleSwipeGesture,
  moveSwipeGesture,
  type SwipeGestureState,
} from "./shared/task-runtime/swipe-nav";
import { taskToolPolicy, type TaskToolPolicy } from "./shared/task-runtime/tool-policy/index";
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
  annotations: AnnotationStroke[];
  elapsedSeconds: number;
  elapsedMs: number;
  material: MockExamLearnerMaterial | null;
}

interface MockAnswerSheetItem {
  index: number;
  label: string;
  state: "answered" | "unanswered";
  marked: boolean;
  active: boolean;
}

interface MockExamPageData extends TaskInteractionProjection {
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
  interactionState: TaskInteractionState;
  questionAnnotations: AnnotationStroke[];
  materialAnnotations: AnnotationStroke[];
  annotationSegments: AnchorSegmentRect[];
  questionScrollPosition: number;
  questionContentHeight: number;
  materialOpen: boolean;
  fontScale: FontScale;
  eyeCare: boolean;
  contentPreferences: ContentDisplayPreferences;
  materialReaderState: MaterialReaderState;
  materialQuestionId: string;
  toolPolicy: TaskToolPolicy;
  pageObjectReference: TaskHelpCommand["pageObjectReference"] | null;
  helpStatus: "idle" | "submitting" | "resolved" | "error";
  helpMessage: string;
  disputeCategories: Array<{ value: MockExamDisputeCategory; label: string }>;
  disputeCategory: MockExamDisputeCategory;
  disputeDescription: string;
  disputeStatus: "idle" | "submitting" | "resolved" | "error";
  disputeMessage: string;
  disputeReceipt: MockExamDisputeReceipt | null;
  receipt: MockExamSubmitReceipt | null;
  reportSessionId: string;
  /** Figma 98:173 交卷确认面板。 */
  submitElapsedLabel: string;
  submitMissingLabels: string[];
  /** 内部题目 scroll-view 的受控位置；0/0.1 交替保证重复切题也能回顶。 */
  questionScrollTop: number;
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
  selectQuestionFromSheet(event: WechatMiniprogram.CustomEvent<{ index: number }>): void;
  toggleAnswerSheet(): void;
  toggleDraft(): void;
  openAnnotation(): void;
  openMaterialAnnotation(): void;
  closeAnnotation(): void;
  changeAnnotations(
    event: WechatMiniprogram.CustomEvent<{
      strokes: AnnotationStroke[];
      action: "added" | "erased";
    }>,
  ): void;
  changeMaterialAnnotations(
    event: WechatMiniprogram.CustomEvent<{
      strokes: AnnotationStroke[];
      action: "added" | "erased";
    }>,
  ): void;
  onQuestionScroll(event: WechatMiniprogram.ScrollViewScroll): void;
  onSegmentRects(event: WechatMiniprogram.CustomEvent<{ segments: AnchorSegmentRect[] }>): void;
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
  decreaseDisplayFont(): void;
  increaseDisplayFont(): void;
  toggleDisplayEyeCare(): void;
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
  /** 中立运行时统一持有起点/末点；多指或松手后整体复位。 */
  swipeGesture: SwipeGestureState;
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
const initialMockInteractionState = createTaskInteractionState();
const initialMockInteractionProjection = projectTaskInteraction(initialMockInteractionState);
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
    interactionState: initialMockInteractionState,
    ...initialMockInteractionProjection,
    questionAnnotations: [] as AnnotationStroke[],
    materialAnnotations: [] as AnnotationStroke[],
    annotationSegments: [] as AnchorSegmentRect[],
    questionScrollPosition: 0,
    questionContentHeight: 0,
    materialOpen: false,
    fontScale: "standard",
    eyeCare: false,
    contentPreferences: { fontScale: 1, colorMode: "standard" },
    materialReaderState: createMaterialReaderState(),
    materialQuestionId: "",
    toolPolicy: mockAnsweringPolicy,
    pageObjectReference: null,
    helpStatus: "idle",
    helpMessage: "",
    disputeCategories,
    disputeCategory: "stem_error",
    disputeDescription: "",
    disputeStatus: "idle",
    disputeMessage: "",
    disputeReceipt: null,
    receipt: null,
    reportSessionId: "",
    submitElapsedLabel: "",
    submitMissingLabels: [],
    questionScrollTop: 0,
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
        errorTitle: "没找到这场模考",
        errorMessage: "从今日计划或报告中心再进一次就好。",
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
    transitionMockInteraction(this, { type: "PAGE_SHOWN" });
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
    transitionMockInteraction(this, { type: "PAGE_HIDDEN" });
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
    transitionMockInteraction(this, { type: "PAGE_UNLOADED" });
    const runtime = runtimes.get(this);
    if (runtime) disposeRuntime(runtime);
    runtimes.delete(this);
    launchContexts.delete(this);
  },

  async loadExam() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    transitionMockInteraction(
      this,
      { type: "LOCK_CHANGED", locked: true },
      {
        status: "loading",
        errorTitle: "",
        errorMessage: "",
        showLoginAction: false,
      },
    );
    try {
      const session = runtime.requestedSessionId
        ? await runtime.api.getSession(runtime.requestedSessionId)
        : await runtime.api.createOrRestoreWeekly(
            await createIdempotencyKey("mock-start"),
            runtime.requestedPlanTaskId || undefined,
          );
      if (runtimes.get(this) !== runtime) return;
      if (runtime.requestedPlanTaskId && !session.planTaskId) {
        throw new Error("这场模考没对上今天的任务，回今日计划再进一次就好。");
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
          errorTitle: "另一台设备正在作答",
          errorMessage: "计时不会暂停；想在这台设备继续的话，重新进入并确认接管就行。",
        });
        return;
      }
      failPage(this, error, "这卷没恢复成功");
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
      leaveMockPage();
      return;
    }
    const result = await wx.showModal({
      title: "计时不会停",
      content: "离开页面计时不会停；回来后会按整场统一时间接着算。",
      confirmText: "仍要离开",
      cancelText: "继续作答",
    });
    if (!result.confirm) return;
    const saved = await saveCheckpoint(this, runtime);
    if (runtimes.get(this) !== runtime) return;
    if (!saved) {
      void wx.showToast({ title: "进度已存在本机，别急，稍后会接着保存", icon: "none" });
    }
    leaveMockPage();
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
    runtime.swipeGesture = beginSwipeGesture(event);
  },

  onQuestionTouchMove(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.swipeGesture.start) return;
    runtime.swipeGesture = moveSwipeGesture(runtime.swipeGesture, event);
  },

  onQuestionTouchEnd(event) {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    const settled = finishSwipeGesture(runtime.swipeGesture, event);
    runtime.swipeGesture = settled.state;
    if (swipeNavigationBlocked(this)) return;
    const direction = settled.direction;
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
    const index = Number(dataset(event, "index"));
    selectMockQuestionAtIndex(this, index);
  },

  selectQuestionFromSheet(event) {
    selectMockQuestionAtIndex(this, event.detail.index);
  },

  toggleAnswerSheet() {
    transitionMockInteraction(this, { type: "OVERLAY_TOGGLED", overlay: "answer_sheet" });
  },

  toggleDraft() {
    if (this.data.status !== "ready" || !this.data.question) return;
    transitionMockInteraction(this, { type: "OVERLAY_TOGGLED", overlay: "draft" });
  },

  // 与行测练习共用题面批注层；笔迹进入同一个 TaskRuntime 检查点。
  openAnnotation() {
    const runtime = runtimes.get(this);
    if (!runtime?.task || runtime.task.phase !== "answering" || this.data.interactionLocked) return;
    transitionMockInteraction(this, { type: "ANNOTATION_OPENED", surface: "question" });
    afterRenderTick(() => {
      if (runtimes.get(this) !== runtime) return;
      measureQuestionContent(this, runtime);
      requestSegmentRects(this);
    });
  },

  closeAnnotation() {
    transitionMockInteraction(this, { type: "ANNOTATION_CLOSED" });
  },

  openMaterialAnnotation() {
    const runtime = runtimes.get(this);
    if (
      !runtime?.task ||
      runtime.task.phase !== "answering" ||
      this.data.interactionLocked ||
      !this.data.question?.material
    ) {
      return;
    }
    transitionMockInteraction(this, { type: "ANNOTATION_OPENED", surface: "material" });
  },

  onQuestionScroll(event) {
    const scrollTop = event.detail.scrollTop;
    if (Number.isFinite(scrollTop) && Math.abs(scrollTop - this.data.questionScrollPosition) >= 1) {
      this.setData({ questionScrollPosition: scrollTop });
    }
  },

  onSegmentRects(event) {
    const segments = Array.isArray(event.detail.segments) ? event.detail.segments : [];
    if (segments.length === 0) return;
    const current = this.data.annotationSegments;
    if (
      current.length === segments.length &&
      current.every((entry, index) => {
        const candidate = segments[index];
        return (
          candidate &&
          entry.blockId === candidate.blockId &&
          entry.segmentIndex === candidate.segmentIndex &&
          Math.abs(entry.top - candidate.top) < 0.5
        );
      })
    ) {
      return;
    }
    this.setData({ annotationSegments: segments });
  },

  changeAnnotations(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    markCurrentQuestionDirty(runtime);
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "ANNOTATIONS_CHANGED",
      strokes: replaceQuestionAnnotations(
        runtime.task.attempts[runtime.task.questionOrder[runtime.task.currentQuestionIndex] ?? ""]
          ?.annotations ?? [],
        event.detail.strokes,
      ),
      action: event.detail.action,
    });
    runtime.mutationVersion += 1;
    applyRuntimeToPage(this, runtime, "待保存");
    scheduleAutosave(this, runtime);
  },

  changeMaterialAnnotations(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    markCurrentQuestionDirty(runtime);
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "ANNOTATIONS_CHANGED",
      strokes: replaceMaterialAnnotations(
        runtime.task.attempts[runtime.task.questionOrder[runtime.task.currentQuestionIndex] ?? ""]
          ?.annotations ?? [],
        event.detail.strokes,
      ),
      action: event.detail.action,
    });
    runtime.mutationVersion += 1;
    applyRuntimeToPage(this, runtime, "待保存");
    scheduleAutosave(this, runtime);
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
    transitionMockInteraction(this, { type: "OVERLAY_TOGGLED", overlay: "tools" });
  },

  openFocusedTool() {
    this.openHelp();
  },

  openHelp() {
    transitionMockInteraction(this, { type: "OVERLAY_OPENED", overlay: "help" });
  },

  closeHelp() {
    transitionMockInteraction(this, { type: "OVERLAY_CLOSED", overlay: "help" });
  },

  async submitHelp(event) {
    const runtime = requiredRuntime(this);
    this.setData({ helpStatus: "submitting", helpMessage: "正在整理刚才的操作现场……" });
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
        helpMessage: errorMessage(error, "帮助没提交上，别走开，再试一次就好。"),
      });
    }
  },

  decreaseDisplayFont() {
    updateDisplayPreferences(this, {
      ...this.data.contentPreferences,
      fontScale: Math.max(
        0.9,
        Math.round((this.data.contentPreferences.fontScale - 0.1) * 10) / 10,
      ),
    });
  },

  increaseDisplayFont() {
    updateDisplayPreferences(this, {
      ...this.data.contentPreferences,
      fontScale: Math.min(
        1.3,
        Math.round((this.data.contentPreferences.fontScale + 0.1) * 10) / 10,
      ),
    });
  },

  toggleDisplayEyeCare() {
    updateDisplayPreferences(this, {
      ...this.data.contentPreferences,
      colorMode: this.data.contentPreferences.colorMode === "eye_care" ? "standard" : "eye_care",
    });
  },

  reportDispute() {
    transitionMockInteraction(
      this,
      { type: "OVERLAY_OPENED", overlay: "dispute" },
      {
        disputeStatus: "idle",
        disputeMessage: "",
        disputeReceipt: null,
      },
    );
  },

  closeDispute() {
    if (this.data.disputeStatus === "submitting") return;
    transitionMockInteraction(this, { type: "OVERLAY_CLOSED", overlay: "dispute" });
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
      this.setData({ disputeStatus: "error", disputeMessage: "先写两句具体问题，至少两个字哦。" });
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
        disputeMessage: "收到啦，我们会去核对这道题。",
        disputeReceipt: receipt,
      });
    } catch (error) {
      if (runtimes.get(this) !== runtime) return;
      if (enterMockAuthRequired(this, runtime, error)) return;
      this.setData({
        disputeStatus: "error",
        disputeMessage: errorMessage(error, "问题没提交上，别走开，再试一次就好。"),
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
    transitionMockInteraction(
      this,
      { type: "OVERLAY_OPENED", overlay: "submit_confirm" },
      {
        submitElapsedLabel: formatRemainingTime(task.totalElapsedSeconds),
        submitMissingLabels: missingLabels,
      },
    );
  },

  closeSubmitConfirm() {
    transitionMockInteraction(this, { type: "OVERLAY_CLOSED", overlay: "submit_confirm" });
  },

  async confirmSubmitExam() {
    if (this.data.status !== "ready" || this.data.interactionLocked) {
      transitionMockInteraction(this, { type: "OVERLAY_CLOSED", overlay: "submit_confirm" });
      return;
    }
    const runtime = requiredRuntime(this);
    transitionMockInteraction(this, { type: "OVERLAY_CLOSED", overlay: "submit_confirm" });
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
    data.status !== "ready" || data.question === null || data.materialOpen || data.swipeBlocked
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
  transitionMockInteraction(page, { type: "QUESTION_CHANGED" });
  applyRuntimeToPage(page, runtime, "待保存");
  resetPageScroll(page);
  scheduleAutosave(page, runtime);
  if (skipped) queueTrajectory(runtime, "skipped", previousQuestionId, { direction: delta });
  queueTrajectory(runtime, "visited", currentQuestionId(runtime), { direction: delta });
}

function selectMockQuestionAtIndex(page: MockExamPageInstance, index: number): void {
  const runtime = requiredRuntime(page);
  const previousQuestionId = currentQuestionId(runtime);
  const previousIndex = requiredTask(runtime).currentQuestionIndex;
  const skipped = currentAttempt(runtime).selectedOptionIds.length === 0;
  markCurrentQuestionDirty(runtime);
  runtime.task = reduceTaskRuntime(requiredTask(runtime), { type: "QUESTION_CHANGED", index });
  runtime.mutationVersion += 1;
  transitionMockInteraction(page, { type: "QUESTION_CHANGED" });
  applyRuntimeToPage(page, runtime, "待保存");
  if (requiredTask(runtime).currentQuestionIndex !== previousIndex) resetPageScroll(page);
  scheduleAutosave(page, runtime);
  if (skipped) {
    queueTrajectory(runtime, "skipped", previousQuestionId, { destinationIndex: index });
  }
  queueTrajectory(runtime, "visited", currentQuestionId(runtime), { source: "answer_sheet" });
}

function updateDisplayPreferences(
  page: MockExamPageInstance,
  contentPreferences: ContentDisplayPreferences,
): void {
  page.setData({
    fontScale: contentPreferences.fontScale > 1 ? "large" : "standard",
    eyeCare: contentPreferences.colorMode === "eye_care",
    contentPreferences,
    materialReaderState: { ...page.data.materialReaderState, preferences: contentPreferences },
  });
  storeTaskDisplayPreferences(contentPreferences);
}

function leaveMockPage(): void {
  if (getCurrentPages().length > 1) {
    void wx.navigateBack();
    return;
  }
  void wx.switchTab({ url: routes.today });
}

function resetPageScroll(page: MockExamPageInstance): void {
  // 微信 scroll-top 同值不会重新触发滚动；与行测工作台相同，在 0/0.1 间交替。
  page.setData({
    questionScrollTop: page.data.questionScrollTop === 0 ? 0.1 : 0,
    questionScrollPosition: 0,
    annotationSegments: [],
  });
}

function afterRenderTick(callback: () => void): void {
  if (typeof wx.nextTick === "function") {
    wx.nextTick(callback);
    return;
  }
  void Promise.resolve().then(callback);
}

/** 批注层与题目 scroll-view 共用内容坐标系，必须以真实 scrollHeight 为基准。 */
function measureQuestionContent(page: MockExamPageInstance, runtime: PageRuntime): void {
  if (!runtime.task || typeof page.createSelectorQuery !== "function") return;
  const query = page.createSelectorQuery();
  query.select(".mock-question-pane").fields({ size: true, scrollOffset: true });
  query.exec((results: Array<{ scrollHeight?: number } | null>) => {
    if (runtimes.get(page) !== runtime) return;
    const scrollHeight = results[0]?.scrollHeight;
    if (scrollHeight && Number.isFinite(scrollHeight) && scrollHeight > 0) {
      page.setData({ questionContentHeight: scrollHeight });
    }
  });
}

function requestSegmentRects(page: MockExamPageInstance): void {
  if (typeof page.selectComponent !== "function") return;
  const renderer = page.selectComponent(".question-stem") as
    { measureSegmentRects?: () => void } | undefined;
  renderer?.measureSegmentRects?.();
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
      state: item.selectedOptionIds.length > 0 ? "answered" : "unanswered",
      marked: item.marked,
      active: index === task.currentQuestionIndex,
    };
  });
  const { question: questionAnnotations, material: materialAnnotations } = splitAnnotationSurfaces(
    attempt.annotations,
  );
  transitionMockInteraction(
    page,
    { type: "LOCK_CHANGED", locked: false },
    {
      status: "ready",
      showLoginAction: false,
      title: session.title,
      reportSessionId: session.sessionId,
      question: {
        ...question,
        options: question.options.map((option) => ({
          ...option,
          content: stripOptionContentPrefix(option.content, option.label),
          selected: attempt.selectedOptionIds.includes(option.optionId),
          eliminated: attempt.eliminatedOptionIds.includes(option.optionId),
        })),
        marked: attempt.marked,
        draftText: attempt.draftText,
        annotations: attempt.annotations ?? [],
        elapsedSeconds: attempt.elapsedSeconds,
        elapsedMs: attempt.elapsedMs ?? attempt.elapsedSeconds * 1000,
        material,
      },
      questionAnnotations,
      materialAnnotations,
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
    },
  );
  afterRenderTick(() => {
    if (runtimes.get(page) !== runtime) return;
    measureQuestionContent(page, runtime);
    requestSegmentRects(page);
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
      transitionMockInteraction(
        page,
        { type: "LOCK_CHANGED", locked: true },
        {
          syncLabel: "另一台设备已接管",
          errorMessage: "别担心，本机的草稿都还在；重新进入并确认接管后，你可以选用哪一份进度。",
        },
      );
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
      errorMessage: errorMessage(error, "哎，这次没存上，别急，再试一次就好。"),
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
        syncLabel: "还在核对上次进度",
        errorMessage: errorMessage(
          error,
          "上次保存的结果还没确定，我们会接着把那次确认完，别担心。",
        ),
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
  transitionMockInteraction(page, { type: "SUBMIT_STARTED" }, { syncLabel: "正在提交" });
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
        transitionMockInteraction(page, { type: "SUBMIT_FAILED" });
        return;
      }
    }
    if (runtimes.get(page) !== runtime) return;
    if (!(await flushTrajectory(runtime))) {
      throw new Error("作答过程还没确认完，请保持网络后重试提交。");
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
    transitionMockInteraction(
      page,
      { type: "SESSION_TERMINAL" },
      {
        status: "submitted",
        syncLabel: "已提交",
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
      },
    );
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
    transitionMockInteraction(
      page,
      lostEditing ? { type: "LOCK_CHANGED", locked: true } : { type: "SUBMIT_FAILED" },
      {
        syncLabel: lostEditing ? "另一台设备已接管" : "提交结果待确认",
        errorMessage: lostEditing
          ? "别担心，本机的提交还保留着；重新进入并确认接管后再交一次就行。"
          : errorMessage(error, "提交结果暂时未知，已原样保留，恢复后会接着确认。"),
      },
    );
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
      applyRuntimeToPage(page, runtime, "已采用云端那份进度");
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
      syncLabel: "进度还没对上",
      errorMessage: errorMessage(error, "刚才的选择没确认上，别走开，再试一次就好。"),
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
  transitionMockInteraction(
    page,
    { type: "SESSION_TERMINAL" },
    {
      status: "submitted",
      title: session.title,
      errorTitle: reviewed ? "本场模考已完成" : "本场模考已经提交",
      errorMessage: reviewed
        ? "已确认提交成功，模考报告可以接着看啦。"
        : "已确认提交成功，可以进模考报告看了。",
      showLoginAction: false,
      question: null,
      syncLabel: reviewed ? "批改已完成" : "提交已确认",
      materialOpen: false,
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
    },
  );
}

function dataset(event: WechatMiniprogram.BaseEvent, name: string): string {
  const value: unknown = event.currentTarget.dataset[name];
  if (typeof value !== "string" && typeof value !== "number") {
    throw new Error(`Mock event requires ${name}`);
  }
  return String(value);
}

function failPage(page: MockExamPageInstance, error: unknown, title: string): void {
  transitionMockInteraction(
    page,
    { type: "LOCK_CHANGED", locked: true },
    {
      status: "error",
      errorTitle: title,
      errorMessage: errorMessage(error, "没找到能继续做的整套卷。"),
    },
  );
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
    swipeGesture: idleSwipeGesture(),
  };
  runtimes.set(page, runtime);
  transitionMockInteraction(
    page,
    { type: "ACCOUNT_CHANGED" },
    {
      showLoginAction: false,
      question: null,
      materialOpen: false,
    },
  );
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
  transitionMockInteraction(
    page,
    { type: "ACCOUNT_CHANGED" },
    {
      status: "unavailable",
      errorTitle: expired ? "登录状态已过期" : "登录后就能开始模考啦",
      errorMessage: expired
        ? `别担心，本机的进度都还在，重新登录就能继续。${requestId ? `（请求 ${requestId}）` : ""}`
        : "模考进度和提交记录要跟着账号走，先登录一下哦。",
      showLoginAction: true,
      question: null,
      syncLabel: expired ? "等你重新登录" : "还没登录",
      materialOpen: false,
    },
  );
}

function transitionMockInteraction(
  page: MockExamPageInstance,
  action: TaskInteractionAction,
  patch: Partial<MockExamPageData> = {},
): void {
  const interactionState = reduceTaskInteraction(page.data.interactionState, action);
  page.setData({
    ...patch,
    interactionState,
    ...projectTaskInteraction(interactionState),
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
  runtime.swipeGesture = idleSwipeGesture();
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
