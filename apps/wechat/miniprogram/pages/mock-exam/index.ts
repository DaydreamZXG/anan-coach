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
import { routes } from "../../app/navigation/routes";
import { isValidPlanTaskId } from "../../features/xingce/real_attempt/contracts";
import { TaskHelpApi, type TaskHelpCommand } from "../../api/task-help";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { createIdempotencyKey } from "../../platform/command-id";
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
  autosaveTimerId?: number;
  mutationVersion: number;
  dirtyQuestionIds: Set<string>;
  savePromise?: Promise<boolean>;
  trajectoryPromise?: Promise<boolean>;
  nextTrajectoryEventSeq: number;
  submitInFlight: boolean;
}

const runtimes = new WeakMap<MockExamPageInstance, PageRuntime>();
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
  },

  onLoad(options: Record<string, string | undefined>) {
    const contentPreferences = loadTaskDisplayPreferences();
    this.setData({
      contentPreferences,
      fontScale: contentPreferences.fontScale > 1 ? "large" : "standard",
      eyeCare: contentPreferences.colorMode === "eye_care",
      materialReaderState: createMaterialReaderState(undefined, contentPreferences),
    });
    const auth = currentAuthSession();
    if (!auth) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能开始整卷模考",
        errorMessage: "模考时间、作答进度和交卷记录必须绑定你的账号。",
        showLoginAction: true,
      });
      return;
    }
    const client = new ApiClient(environment.apiBaseUrl, auth.accessToken);
    const requestedSessionId = options.sessionId?.trim() ?? "";
    const requestedPlanTaskId = options.planTaskId?.trim() ?? "";
    if (
      (requestedSessionId && requestedPlanTaskId) ||
      (requestedPlanTaskId && !isValidPlanTaskId(requestedPlanTaskId))
    ) {
      this.setData({
        status: "unavailable",
        errorTitle: "无法确认本次模考任务",
        errorMessage: "请从今日计划或报告中心重新进入。",
      });
      return;
    }
    this.setData({ reportSessionId: requestedSessionId });
    runtimes.set(this, {
      ownerUserId: auth.userId,
      api: new MockExamApi(client),
      helpApi: new TaskHelpApi(client),
      editingApi: new TaskEditingApi(client),
      requestedSessionId,
      requestedPlanTaskId,
      visible: false,
      disposed: false,
      mutationVersion: 0,
      dirtyQuestionIds: new Set<string>(),
      nextTrajectoryEventSeq: 1,
      submitInFlight: false,
    });
    void this.loadExam();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    if (currentAuthSession()?.userId !== runtime.ownerUserId) {
      invalidateForAccountChange(this, runtime);
      return;
    }
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
    if (!runtime) return;
    runtime.disposed = true;
    stopClock(runtime);
    clearAutosave(runtime);
    runtimes.delete(this);
  },

  async loadExam() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    this.setData({ status: "loading", errorTitle: "", errorMessage: "" });
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
        runtime.session = session;
        delete runtime.task;
        stopClock(runtime);
        this.setData({
          status: "submitted",
          title: session.title,
          reportSessionId: session.sessionId,
          interactionLocked: true,
          syncLabel: "本场已交卷",
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
          errorMessage: "本场模考已经交卷，答案解析只在正式报告页展示。",
        });
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
    void this.loadExam();
  },

  goLogin() {
    void wx.navigateTo({ url: "/pages/login/index" });
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

  selectQuestion(event) {
    const runtime = requiredRuntime(this);
    const index = Number(dataset(event, "index"));
    const previousQuestionId = currentQuestionId(runtime);
    const skipped = currentAttempt(runtime).selectedOptionIds.length === 0;
    markCurrentQuestionDirty(runtime);
    runtime.task = reduceTaskRuntime(requiredTask(runtime), { type: "QUESTION_CHANGED", index });
    runtime.mutationVersion += 1;
    // 交卷确认面板中点未答题号 = 回去检查该题（Figma 98:192）。
    this.setData({ answerSheetOpen: false, submitConfirmOpen: false });
    applyRuntimeToPage(this, runtime, "待保存");
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
    const materialReaderState = reduceMaterialReader(
      this.data.materialReaderState,
      this.data.materialOpen ? { type: "RETURN_TO_QUESTION" } : { type: "OPEN_DRAWER" },
    );
    this.setData({
      materialOpen: materialReaderState.mode !== "question_first",
      materialReaderState,
    });
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
      this.setData({
        disputeStatus: "error",
        disputeMessage: errorMessage(error, "争议没有提交成功，请保留当前页面后重试。"),
      });
    }
  },

  // Figma 98:2：底部确认面板取代系统弹窗，展示真实的已答/未答/标记与未答题号。
  submitExam() {
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
  scheduleAutosave(page, runtime);
  if (skipped) queueTrajectory(runtime, "skipped", previousQuestionId, { direction: delta });
  queueTrajectory(runtime, "visited", currentQuestionId(runtime), { direction: delta });
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
    clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
    if (runtimes.get(page) !== runtime) return true;
    const updated = sessionWithCheckpointReceipt(session, receipt);
    runtime.session = updated;
    runtime.task =
      runtime.mutationVersion === sentMutationVersion
        ? runtimeFromMockSession(updated, Date.now())
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
      clearPendingMockCheckpoint(runtime.ownerUserId, pending.sessionId);
      if (runtime.disposed) return true;
      const updated = sessionWithCheckpointReceipt(requiredSession(runtime), receipt);
      runtime.session = updated;
      runtime.task = runtimeFromMockSession(updated, Date.now());
      pending.command.answers.forEach((answer) => {
        runtime.dirtyQuestionIds.delete(answer.questionId);
      });
      applyRuntimeToPage(page, runtime, "已确认上次进度");
      return true;
    } catch (error) {
      if (!runtime.disposed) {
        page.setData({
          syncLabel: "上次进度待确认",
          errorMessage: errorMessage(error, "上次保存结果未知，将继续确认刚才那次保存。"),
        });
      }
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
  if (runtime.submitInFlight) return;
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
    const receipt = await submitWithLease(runtime, submission);
    const committedSession = requiredSession(runtime);
    const task = requiredTask(runtime);
    clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
    clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
    if (runtime.disposed) return;
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
    if (runtime.disposed) return;
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
): Promise<MockExamSubmitReceipt> {
  try {
    return await runtime.api.submit(
      submission.sessionId,
      submission.command,
      submission.idempotencyKey,
      requiredMockEditToken(runtime),
    );
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
    runtime.editOwnership = resolved;
    const latest = await runtime.api.getSession(submission.sessionId);
    if (latest.status !== "assigned") {
      clearPendingMockSubmit(runtime.ownerUserId, submission.sessionId);
      throw new Error("云端已经完成交卷，当前设备不再重复提交。", { cause: error });
    }
    const serverTask = runtimeFromMockSession(latest, Date.now());
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
    return runtime.api.submit(
      rebased.sessionId,
      rebased.command,
      rebased.idempotencyKey,
      requiredMockEditToken(runtime),
    );
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
  runtime.timerId = setInterval(() => {
    refreshClock(page, runtime);
  }, 1000);
}

function stopClock(runtime: PageRuntime): void {
  if (runtime.timerId === undefined) return;
  clearInterval(runtime.timerId);
  delete runtime.timerId;
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
      removePendingMockTrajectory(runtime.ownerUserId, item.sessionId, item.idempotencyKey);
    } catch (error) {
      if (!(error instanceof ApiClientError) || error.statusCode !== 409 || rebased) return false;
      try {
        const latest = await runtime.api.getSession(session.sessionId);
        if (latest.status !== "assigned") {
          replacePendingMockTrajectory(runtime.ownerUserId, session.sessionId, []);
          return true;
        }
        runtime.session = latest;
        rebaseTrajectory(runtime, latest.nextTrajectoryEventSeq);
        rebased = true;
      } catch {
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
    clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
    storePendingMockCheckpoint({
      ...checkpoint,
      idempotencyKey: await createIdempotencyKey("mock-checkpoint-resolved"),
      command: buildMockCheckpointCommand(localTask),
      storedAt,
    });
  }
  if (submission) {
    localTask = runtimeWithPendingMockSubmit(localTask, submission.command);
    clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
    storePendingMockSubmit({
      ...submission,
      idempotencyKey: await createIdempotencyKey("mock-submit-resolved"),
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
    runtime.editOwnership = resolved;
    const latest = await runtime.api.getSession(ownership.lease.resourceId);
    clearPendingMockCheckpoint(runtime.ownerUserId, ownership.lease.resourceId);
    if (latest.status !== "assigned") {
      runtime.session = latest;
      delete runtime.task;
      stopClock(runtime);
      page.setData({
        status: "submitted",
        interactionLocked: true,
        syncLabel: "本场已交卷",
        reportSessionId: latest.sessionId,
        errorMessage: "云端已结束本场模考，当前设备不再写入离线草稿。",
      });
      return true;
    }
    runtime.session = latest;
    if (resolved.conflictChoice === "use_server") {
      runtime.task = runtimeFromMockSession(latest, Date.now());
      runtime.dirtyQuestionIds.clear();
      applyRuntimeToPage(page, runtime, "已使用云端进度");
      return true;
    }
    const localCommand = buildMockCheckpointCommand(localTask);
    runtime.task = runtimeWithPendingMockCheckpoint(
      runtimeFromMockSession(latest, Date.now()),
      localCommand,
    );
    runtime.dirtyQuestionIds = new Set(runtime.task.questionOrder);
    runtime.mutationVersion += 1;
    const command = buildMockCheckpointCommand(runtime.task);
    const idempotencyKey = await createIdempotencyKey("mock-checkpoint-resolved");
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
    clearPendingMockCheckpoint(runtime.ownerUserId, latest.sessionId);
    const updated = sessionWithCheckpointReceipt(latest, receipt);
    runtime.session = updated;
    runtime.task = runtimeFromMockSession(updated, Date.now());
    runtime.dirtyQuestionIds.clear();
    applyRuntimeToPage(page, runtime, "已按你的选择保留本机草稿");
    return true;
  } catch (error) {
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

function invalidateForAccountChange(page: MockExamPageInstance, runtime: PageRuntime): void {
  runtime.disposed = true;
  stopClock(runtime);
  clearAutosave(runtime);
  runtimes.delete(page);
  page.setData({
    status: "unavailable",
    errorTitle: "账号已经变更",
    errorMessage: "旧账号的整卷和进度已从当前页面清除，请用当前账号重新进入。",
    showLoginAction: true,
    question: null,
    interactionLocked: true,
    answerSheetOpen: false,
    materialOpen: false,
    toolsOpen: false,
    helpOpen: false,
    disputeOpen: false,
  });
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}
