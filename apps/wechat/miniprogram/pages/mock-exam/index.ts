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
import { routes } from "../../app/navigation/routes";
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
  submitExam(): Promise<void>;
  returnToday(): void;
  openMockReport(): void;
  noop(): void;
}

type MockExamPageInstance = WechatMiniprogram.Page.Instance<MockExamPageData, MockExamPageCustom>;

interface PageRuntime {
  ownerUserId: string;
  api: MockExamApi;
  helpApi: TaskHelpApi;
  requestedSessionId: string;
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
    syncLabel: "连接服务端",
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
  },

  onLoad(options: Record<string, string | undefined>) {
    const contentPreferences = loadTaskDisplayPreferences();
    this.setData({
      contentPreferences,
      fontScale: contentPreferences.fontScale > 1 ? "large" : "standard",
      eyeCare: contentPreferences.colorMode === "eye_care",
      materialReaderState: createMaterialReaderState(undefined, contentPreferences),
    });
    if (environment.dataMode === "prototype") {
      this.setData({
        status: "unavailable",
        errorTitle: "演示数据不能进入整卷模考",
        errorMessage: "整卷、服务端绝对计时和断点必须绑定真实账号与真实题面版本。",
      });
      return;
    }
    const auth = currentAuthSession();
    if (!auth) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能开始整卷模考",
        errorMessage: "模考时间、断点和交卷证据必须绑定你的账号。",
        showLoginAction: true,
      });
      return;
    }
    const client = new ApiClient(environment.apiBaseUrl, auth.accessToken);
    runtimes.set(this, {
      ownerUserId: auth.userId,
      api: new MockExamApi(client),
      helpApi: new TaskHelpApi(client),
      requestedSessionId: options.sessionId?.trim() ?? "",
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
        : await runtime.api.createOrRestoreWeekly(await createIdempotencyKey("mock-start"));
      if (runtimes.get(this) !== runtime) return;
      if (session.status !== "assigned") {
        this.setData({
          status: "submitted",
          title: session.title,
          errorMessage: "本场模考已经交卷，答案解析只在正式报告页展示。",
        });
        return;
      }
      runtime.session = session;
      runtime.task = runtimeFromMockSession(session, Date.now());
      initializeTrajectory(runtime, session);
      applyRuntimeToPage(this, runtime, "已恢复服务端断点");
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
        applyRuntimeToPage(this, runtime, "正在确认上次断点");
        void resumePendingCheckpoint(this, runtime, pendingCheckpoint);
      }
      refreshClock(this, runtime);
      startClock(this, runtime);
    } catch (error) {
      if (runtimes.get(this) !== runtime) return;
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
      content: "离开页面不会暂停计时；回来后将按服务端截止时间恢复。",
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
    this.setData({ answerSheetOpen: false });
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
    this.setData({ disputeStatus: "submitting", disputeMessage: "正在绑定本场固定题面证据……" });
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
        disputeMessage: `争议已记录 · ${receipt.disputeId}`,
        disputeReceipt: receipt,
      });
    } catch (error) {
      if (runtimes.get(this) !== runtime) return;
      this.setData({
        disputeStatus: "error",
        disputeMessage: errorMessage(error, "争议未写入服务端，请保留当前页面后重试。"),
      });
    }
  },

  async submitExam() {
    const runtime = requiredRuntime(this);
    const counts = countMockAnswers(requiredTask(runtime));
    const result = await wx.showModal({
      title: "确认交卷",
      content:
        counts.unanswered > 0
          ? `还有 ${String(counts.unanswered)} 题未答。交卷后不能继续修改，是否提交？`
          : "交卷后不能继续修改，是否提交？",
      confirmText: "确认交卷",
      cancelText: "继续检查",
    });
    if (result.confirm) await executeSubmit(this, runtime);
  },

  returnToday() {
    void wx.switchTab({ url: "/pages/today/index" });
  },

  openMockReport() {
    const runtime = runtimes.get(this);
    const sessionId = runtime?.session?.sessionId ?? runtime?.requestedSessionId;
    void wx.navigateTo({
      url: sessionId
        ? `${routes.mockReport}?sessionId=${encodeURIComponent(sessionId)}`
        : routes.mockReport,
    });
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
    const receipt = await runtime.api.saveCheckpoint(session.sessionId, command, idempotencyKey);
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
    runtime.task = reduceTaskRuntime(requiredTask(runtime), {
      type: "SAVE_FAILED",
      requestId: error instanceof ApiClientError ? error.requestId : "mock-checkpoint-failed",
    });
    page.setData({
      syncLabel: "保存失败",
      errorMessage: errorMessage(error, "断点没有得到服务端确认，请重试后再离开。"),
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
      );
      clearPendingMockCheckpoint(runtime.ownerUserId, pending.sessionId);
      if (runtime.disposed) return true;
      const updated = sessionWithCheckpointReceipt(requiredSession(runtime), receipt);
      runtime.session = updated;
      runtime.task = runtimeFromMockSession(updated, Date.now());
      pending.command.answers.forEach((answer) => {
        runtime.dirtyQuestionIds.delete(answer.questionId);
      });
      applyRuntimeToPage(page, runtime, "已确认上次断点");
      return true;
    } catch (error) {
      if (!runtime.disposed) {
        page.setData({
          syncLabel: "上次断点待确认",
          errorMessage: errorMessage(error, "上次保存结果未知，将保留原命令继续确认。"),
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
      throw new Error("考试轨迹尚未得到服务端确认，请保持网络后重试交卷。");
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
    const receipt = await submitWithRevisionRecovery(runtime, submission);
    if (!receipt) {
      clearPendingMockSubmit(runtime.ownerUserId, session.sessionId);
      clearPendingMockCheckpoint(runtime.ownerUserId, session.sessionId);
      stopClock(runtime);
      page.setData({
        status: "submitted",
        interactionLocked: true,
        syncLabel: "已在其他设备交卷",
        errorMessage: "服务端确认本场已经交卷，请从正式模考报告查看结果。",
        toolPolicy: taskToolPolicy("mock", "submitted"),
      });
      return;
    }
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
    page.setData({
      interactionLocked: false,
      syncLabel: "交卷结果待确认",
      errorMessage: errorMessage(error, "交卷结果未知，已保留同一命令供恢复时继续确认。"),
    });
  } finally {
    runtime.submitInFlight = false;
  }
}

async function submitWithRevisionRecovery(
  runtime: PageRuntime,
  submission: PendingMockSubmit,
): Promise<MockExamSubmitReceipt | null> {
  try {
    return await runtime.api.submit(
      submission.sessionId,
      submission.command,
      submission.idempotencyKey,
    );
  } catch (error) {
    if (!(error instanceof ApiClientError) || error.statusCode !== 409) throw error;
    const latest = await runtime.api.getSession(submission.sessionId);
    runtime.session = latest;
    if (latest.status !== "assigned") return null;
    runtime.task = runtimeWithServerRevision(requiredTask(runtime), latest);
    const refreshed: PendingMockSubmit = {
      kind: "submit",
      ownerUserId: runtime.ownerUserId,
      sessionId: latest.sessionId,
      idempotencyKey: await createIdempotencyKey("mock-submit"),
      command: buildMockSubmitCommand(requiredTask(runtime)),
      storedAt: new Date().toISOString(),
    };
    storePendingMockSubmit(refreshed);
    return runtime.api.submit(refreshed.sessionId, refreshed.command, refreshed.idempotencyKey);
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
    errorMessage: errorMessage(error, "服务端没有返回可继续作答的整卷。"),
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
    errorMessage: "旧账号的整卷题面和断点已从当前页面清除，请用当前账号重新进入。",
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
