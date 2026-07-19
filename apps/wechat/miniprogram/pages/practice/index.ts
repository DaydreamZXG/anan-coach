import { ApiClient, ApiClientError } from "../../api/client";
import { MathInkApi, type FormulaVersion, type InkStroke } from "../../api/math-ink";
import { TaskEditingApi } from "../../api/task-editing";
import {
  XingceLearningApi,
  type XingceQuestionDisputeCategory,
  type XingceTaskSession,
} from "../../api/xingce-learning";
import {
  TaskHelpApi,
  type TaskHelpCommand,
  type TaskHelpPageObjectReference,
} from "../../api/task-help";
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
import {
  acquireTaskEditOwnership,
  resolveRevisionConflict,
  TaskEditTakeoverDeclined,
} from "../../features/task-editing/coordinator";
import { createIdempotencyKey } from "../../platform/command-id";
import { decideAutosave, decideExit } from "../../task-runtime/state/autosave";
import { reduceTaskRuntime, unansweredQuestionIds } from "../../task-runtime/state/runtime";
import type { ContentDisplayPreferences } from "../../task-runtime/content-renderer/model";
import {
  loadTaskDisplayPreferences,
  storeTaskDisplayPreferences,
} from "../../task-runtime/display-preferences";
import {
  createMaterialReaderState,
  reduceMaterialReader,
  type MaterialReaderState,
} from "../../task-runtime/material-reader/state";
import { taskToolPolicy, type TaskToolPolicy } from "../../task-runtime/tool-policy/index";

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
  materialQuestionId: string;
  materialReaderState: MaterialReaderState;
  displayPreferences: ContentDisplayPreferences;
  toolsOpen: boolean;
  helpOpen: boolean;
  disputeOpen: boolean;
  toolPolicy: TaskToolPolicy;
  helpReference: TaskHelpPageObjectReference | null;
  helpSyncState: TaskHelpCommand["diagnostics"]["syncState"];
  helpSubmitting: boolean;
  helpGuidance: string;
  disputeCategory: XingceQuestionDisputeCategory;
  disputeQuestionId: string;
  disputeDescription: string;
  disputeSubmitting: boolean;
  disputeReceipt: string;
  disputeCategories: Array<{ value: XingceQuestionDisputeCategory; label: string }>;
  answerSheetOpen: boolean;
  draftOpen: boolean;
  mathInkOpen: boolean;
  mathInkRecognizing: boolean;
  mathInkCandidate: FormulaVersion | null;
  mathInkLatex: string;
  mathInkError: string;
  taskPaused: boolean;
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
  updateMaterialReader(event: WechatMiniprogram.CustomEvent<{ state: MaterialReaderState }>): void;
  openTaskTools(): void;
  openFocusTool(): void;
  closeTaskOverlay(): void;
  openTaskHelp(): void;
  submitTaskHelp(event: WechatMiniprogram.CustomEvent<TaskHelpCommand>): Promise<void>;
  openDispute(): void;
  openResultDispute(event: WechatMiniprogram.BaseEvent): void;
  selectDisputeCategory(event: WechatMiniprogram.BaseEvent): void;
  inputDisputeDescription(event: WechatMiniprogram.Input): void;
  submitDispute(): Promise<void>;
  increaseDisplayFont(): void;
  decreaseDisplayFont(): void;
  toggleDisplayEyeCare(): void;
  toggleAnswerSheet(): void;
  toggleDraft(): void;
  toggleTaskPause(): void;
  selectQuestion(event: WechatMiniprogram.BaseEvent): void;
  selectOption(event: WechatMiniprogram.BaseEvent): void;
  toggleEliminated(event: WechatMiniprogram.BaseEvent): void;
  selectConfidence(event: WechatMiniprogram.BaseEvent): void;
  toggleMarked(): void;
  skipQuestion(): void;
  inputDraft(event: WechatMiniprogram.Input): void;
  toggleMathInk(): void;
  recognizeMathInk(event: WechatMiniprogram.CustomEvent<{ strokes: InkStroke[] }>): Promise<void>;
  inputMathInkLatex(event: WechatMiniprogram.Input): void;
  confirmMathInk(): Promise<void>;
  previousQuestion(): void;
  nextQuestion(): void;
  footerAction(): void;
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
    materialQuestionId: "",
    materialReaderState: createMaterialReaderState(),
    displayPreferences: { fontScale: 1, colorMode: "standard" },
    toolsOpen: false,
    helpOpen: false,
    disputeOpen: false,
    toolPolicy: taskToolPolicy("targeted", "answering"),
    helpReference: null,
    helpSyncState: "unknown",
    helpSubmitting: false,
    helpGuidance: "",
    disputeCategory: "other",
    disputeQuestionId: "",
    disputeDescription: "",
    disputeSubmitting: false,
    disputeReceipt: "",
    disputeCategories: [
      { value: "stem_error", label: "题干有误" },
      { value: "option_error", label: "选项有误" },
      { value: "answer_error", label: "答案争议" },
      { value: "material_mismatch", label: "材料不匹配" },
      { value: "image_unreadable", label: "图片不清" },
      { value: "other", label: "其他" },
    ],
    answerSheetOpen: false,
    draftOpen: false,
    mathInkOpen: false,
    mathInkRecognizing: false,
    mathInkCandidate: null,
    mathInkLatex: "",
    mathInkError: "",
    taskPaused: false,
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
    const displayPreferences = loadTaskDisplayPreferences();
    this.setData({
      displayPreferences,
      materialReaderState: createMaterialReaderState(undefined, displayPreferences),
    });
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
    const client = new ApiClient(environment.apiBaseUrl, auth.accessToken);
    runtime.api = new XingceLearningApi(client);
    runtime.editingApi = new TaskEditingApi(client);
    void this.loadTask();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    runtime.visible = true;
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
    runtime.visible = false;
    stopTimer(runtime);
    if (runtime.task) void autosaveCheckpoint(this, runtime, { leaving: true });
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
        const localOverride = await acquireEditingForSession(runtime, response.data);
        if (!runtimes.isCurrent(this, runtime, epoch)) return;
        applySession(this, runtime, response.data, localOverride);
        if (localOverride) await saveCheckpoint(this, runtime);
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
      const localOverride = await acquireEditingForSession(runtime, response.data);
      if (!runtimes.isCurrent(this, runtime, epoch)) return;
      applySession(this, runtime, response.data, localOverride);
      if (localOverride) await saveCheckpoint(this, runtime);
    } catch (error) {
      if (!runtimes.isCurrent(this, runtime, epoch)) return;
      if (error instanceof TaskEditTakeoverDeclined) {
        this.setData({
          status: "unavailable",
          errorTitle: "此设备没有编辑权",
          errorMessage: "任务仍由另一台设备作答。需要在此设备继续时，请重新进入并确认接管。",
        });
        return;
      }
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
    const decision = decideExit(runtime.task);
    if (decision.action === "blocked") {
      const result = await wx.showModal({
        title: decision.title,
        content: `${decision.message} 你可以稍后从今日计划继续。`,
        confirmText: "仍要退出",
      });
      if (!result.confirm) return;
    } else if (decision.action === "save_then_leave") {
      await saveCheckpoint(this, runtime);
      const currentTask = runtimes.get(this)?.task;
      if (currentTask?.syncState !== "saved") return;
    } else if (decision.action === "confirm") {
      const result = await wx.showModal({
        title: decision.title,
        content: decision.message,
        confirmText: "退出",
      });
      if (!result.confirm) return;
    }
    void wx.navigateBack();
  },

  toggleMaterial() {
    const event =
      this.data.materialReaderState.mode === "question_first"
        ? ({ type: "OPEN_DRAWER" } as const)
        : ({ type: "RETURN_TO_QUESTION" } as const);
    const materialReaderState = reduceMaterialReader(this.data.materialReaderState, event);
    this.setData({
      materialOpen: materialReaderState.mode !== "question_first",
      materialReaderState,
    });
  },

  updateMaterialReader(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    const next = event.detail.state;
    const questionId = runtime.task.questionOrder[runtime.task.currentQuestionIndex];
    const attempt = questionId ? runtime.task.attempts[questionId] : undefined;
    if (!attempt) return;
    const preferencesChanged =
      next.preferences.fontScale !== this.data.displayPreferences.fontScale ||
      next.preferences.colorMode !== this.data.displayPreferences.colorMode;
    this.setData({
      materialOpen: next.mode !== "question_first",
      materialReaderState: next,
      displayPreferences: next.preferences,
    });
    if (preferencesChanged) storeTaskDisplayPreferences(next.preferences);
    const current = attempt.materialPosition;
    if (
      current?.anchorId === next.position.anchorId &&
      current.scrollTop === next.position.scrollTop &&
      current.zoom === next.position.zoom
    ) {
      return;
    }
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "MATERIAL_POSITION_CHANGED",
      position: next.position,
    });
    void autosaveCheckpoint(this, runtime);
  },

  openTaskTools() {
    const runtime = runtimes.get(this);
    if (!runtime?.session || runtime.session.state !== "answering") return;
    this.setData({
      toolsOpen: true,
      helpOpen: false,
      disputeOpen: false,
      draftOpen: false,
      answerSheetOpen: false,
    });
  },

  openFocusTool() {
    this.openTaskHelp();
  },

  closeTaskOverlay() {
    this.setData({ toolsOpen: false, helpOpen: false, disputeOpen: false, draftOpen: false });
  },

  openTaskHelp() {
    const runtime = runtimes.get(this);
    if (!runtime?.session) return;
    this.setData({
      toolsOpen: false,
      helpOpen: true,
      disputeOpen: false,
      helpReference: taskHelpReference(runtime.session),
      helpGuidance: "",
    });
  },

  async submitTaskHelp(event) {
    if (this.data.helpSubmitting) return;
    const auth = currentAuthSession();
    if (!auth) return;
    this.setData({ helpSubmitting: true, helpGuidance: "" });
    try {
      const api = new TaskHelpApi(new ApiClient(environment.apiBaseUrl, auth.accessToken));
      const response = await api.submit(event.detail, await createIdempotencyKey("task-help"));
      this.setData({ helpGuidance: `${response.data.title}：${response.data.steps.join("；")}` });
    } catch (error) {
      this.setData({ helpGuidance: errorMessage(error) });
    } finally {
      this.setData({ helpSubmitting: false });
    }
  },

  openDispute() {
    const runtime = runtimes.get(this);
    if (!runtime?.session || runtime.session.state !== "answering") return;
    const questionId = this.data.view?.question.questionId;
    if (questionId) showDispute(this, questionId);
  },

  openResultDispute(event) {
    const runtime = runtimes.get(this);
    const questionId = String(event.currentTarget.dataset.questionid ?? "");
    if (
      !runtime?.session?.result?.items.some((item) => item.questionId === questionId) ||
      runtime.session.state === "answering"
    ) {
      return;
    }
    showDispute(this, questionId);
  },

  selectDisputeCategory(event) {
    const value = String(event.currentTarget.dataset.category ?? "");
    const category = this.data.disputeCategories.find((item) => item.value === value)?.value;
    if (category) this.setData({ disputeCategory: category });
  },

  inputDisputeDescription(event) {
    this.setData({ disputeDescription: event.detail.value });
  },

  async submitDispute() {
    const runtime = runtimes.get(this);
    const questionId = this.data.disputeQuestionId || this.data.view?.question.questionId;
    const question = runtime?.session?.bundle.questions.find(
      (candidate) => candidate.questionId === questionId,
    );
    const description = this.data.disputeDescription.trim();
    if (!runtime?.api || !runtime.session || !question || this.data.disputeSubmitting) return;
    if (description.length < 2) {
      void wx.showToast({ title: "请描述具体问题", icon: "none" });
      return;
    }
    this.setData({ disputeSubmitting: true, disputeReceipt: "" });
    try {
      const response = await runtime.api.reportDispute(
        runtime.session.sessionId,
        {
          questionId: question.questionId,
          contentVersionId: question.contentVersionId,
          category: this.data.disputeCategory,
          description,
        },
        await createIdempotencyKey("xingce-dispute"),
      );
      this.setData({
        disputeReceipt: `已记录 · ${response.data.disputeId}`,
        disputeDescription: "",
      });
      if (runtime.session.state !== "answering") {
        const refreshed = await runtime.api.getSession(runtime.session.sessionId);
        if (!runtimes.owns(this, runtime) || !currentAccountOwns(runtime)) return;
        applySession(this, runtime, refreshed.data);
        this.setData({
          disputeOpen: true,
          disputeReceipt: `已记录 · ${response.data.disputeId}·复测证据状态已刷新`,
        });
      }
    } catch (error) {
      this.setData({ disputeReceipt: errorMessage(error) });
    } finally {
      this.setData({ disputeSubmitting: false });
    }
  },

  increaseDisplayFont() {
    updateDisplayPreferences(this, {
      ...this.data.displayPreferences,
      fontScale: Math.min(
        1.3,
        Math.round((this.data.displayPreferences.fontScale + 0.1) * 10) / 10,
      ),
    });
  },

  decreaseDisplayFont() {
    updateDisplayPreferences(this, {
      ...this.data.displayPreferences,
      fontScale: Math.max(
        0.9,
        Math.round((this.data.displayPreferences.fontScale - 0.1) * 10) / 10,
      ),
    });
  },

  toggleDisplayEyeCare() {
    updateDisplayPreferences(this, {
      ...this.data.displayPreferences,
      colorMode: this.data.displayPreferences.colorMode === "eye_care" ? "standard" : "eye_care",
    });
  },

  toggleAnswerSheet() {
    this.setData({
      answerSheetOpen: !this.data.answerSheetOpen,
      draftOpen: false,
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
    });
  },

  toggleDraft() {
    const runtime = runtimes.get(this);
    if (!runtime?.session || runtime.session.state !== "answering") return;
    this.setData({
      draftOpen: !this.data.draftOpen,
      answerSheetOpen: false,
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
    });
  },

  toggleTaskPause() {
    const runtime = runtimes.get(this);
    if (!runtime?.task || runtime.task.phase !== "answering") return;
    const taskPaused = !this.data.taskPaused;
    this.setData({
      taskPaused,
      answerSheetOpen: false,
      draftOpen: false,
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
    });
    if (taskPaused) {
      stopTimer(runtime);
      void autosaveCheckpoint(this, runtime);
    } else {
      startTimer(this, runtime);
    }
  },

  footerAction() {
    const view = this.data.view;
    if (!view || this.data.interactionLocked) return;
    if (view.current < view.total) {
      this.nextQuestion();
      return;
    }
    if (this.data.canSubmit) {
      void this.submitAttempt();
      return;
    }
    this.toggleAnswerSheet();
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

  toggleMathInk() {
    const runtime = runtimes.get(this);
    if (!runtime?.session || runtime.session.state !== "answering") return;
    this.setData({
      mathInkOpen: !this.data.mathInkOpen,
      mathInkCandidate: null,
      mathInkLatex: "",
      mathInkError: "",
    });
  },

  async recognizeMathInk(event) {
    const runtime = runtimes.get(this);
    const questionId = runtime?.task?.questionOrder[runtime.task.currentQuestionIndex];
    const auth = currentAuthSession();
    if (!runtime?.session || !questionId || !auth || this.data.mathInkRecognizing) return;
    this.setData({ mathInkRecognizing: true, mathInkCandidate: null, mathInkError: "" });
    try {
      const result = await new MathInkApi(
        new ApiClient(environment.apiBaseUrl, auth.accessToken),
      ).recognize(
        {
          sessionId: runtime.session.sessionId,
          questionId,
          scaleX: 0.264583,
          scaleY: 0.264583,
          strokes: event.detail.strokes,
        },
        await createIdempotencyKey("math-ink"),
      );
      if (result.state !== "candidate" || !result.formula) {
        this.setData({
          mathInkRecognizing: false,
          mathInkError: "这次没有形成可确认公式，请清空后重新书写。",
        });
        return;
      }
      this.setData({
        mathInkRecognizing: false,
        mathInkCandidate: result.formula,
        mathInkLatex: result.formula.latex,
      });
    } catch (error) {
      this.setData({
        mathInkRecognizing: false,
        mathInkError: error instanceof ApiClientError ? error.message : "公式识别失败，请重试。",
      });
    }
  },

  inputMathInkLatex(event) {
    this.setData({ mathInkLatex: event.detail.value });
  },

  async confirmMathInk() {
    const runtime = runtimes.get(this);
    const auth = currentAuthSession();
    const candidate = this.data.mathInkCandidate;
    const latex = this.data.mathInkLatex.trim();
    if (!runtime?.task || !auth || !candidate || !latex || this.data.mathInkRecognizing) return;
    this.setData({ mathInkRecognizing: true, mathInkError: "" });
    try {
      const confirmed = await new MathInkApi(
        new ApiClient(environment.apiBaseUrl, auth.accessToken),
      ).confirm(candidate, latex);
      const questionId = runtime.task.questionOrder[runtime.task.currentQuestionIndex];
      const attempt = questionId ? runtime.task.attempts[questionId] : undefined;
      const prefix = attempt?.draftText.trim() ? `${attempt.draftText.trim()}\n` : "";
      runtime.task = reduceTaskRuntime(runtime.task, {
        type: "DRAFT_CHANGED",
        text: `${prefix}公式：\\(${confirmed.latex}\\)`,
      });
      this.setData({
        mathInkRecognizing: false,
        mathInkOpen: false,
        mathInkCandidate: null,
        mathInkLatex: "",
      });
      render(this, runtime);
      await saveCheckpoint(this, runtime);
    } catch (error) {
      this.setData({
        mathInkRecognizing: false,
        mathInkError: error instanceof ApiClientError ? error.message : "公式确认失败，请重试。",
      });
    }
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
        requiredEditToken(runtime),
      );
      clearPendingXingceSubmit(runtime.ownerUserId, pending.sessionId);
      applySession(this, runtime, response.data);
      stopTimer(runtime);
    } catch (error) {
      if (
        error instanceof ApiClientError &&
        error.statusCode === 409 &&
        error.payload?.code === "task_edit_owned_elsewhere"
      ) {
        runtime.editOwnership = null;
        stopTimer(runtime);
        this.setData({
          errorMessage: "另一台设备已接管本任务。本机交卷命令仍保留；重新进入并确认接管后再提交。",
        });
        return;
      }
      handleInlineError(this, runtime, error, "交卷结果尚未确认，请重试同一命令。");
    } finally {
      runtime.submitInFlight = false;
      if (runtimes.owns(this, runtime)) {
        this.setData({
          interactionLocked:
            runtime.session.state === "answering" && runtime.editOwnership === null,
        });
      }
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

function showDispute(page: PracticePageInstance, questionId: string): void {
  page.setData({
    toolsOpen: false,
    helpOpen: false,
    disputeOpen: true,
    disputeQuestionId: questionId,
    disputeCategory: "other",
    disputeDescription: "",
    disputeReceipt: "",
  });
}

function applySession(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
  session: XingceTaskSession,
  localOverride: ReturnType<typeof runtimeFromXingceSession> | null = null,
): void {
  runtime.session = session;
  runtime.planTaskId = session.planTaskId;
  const pending = localOverride
    ? null
    : loadPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
  const retryablePending = session.state === "answering" ? pending : null;
  if (pending && !retryablePending) {
    clearPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
  }
  runtime.task =
    localOverride ??
    (retryablePending
      ? runtimeWithPendingCheckpoint(session, retryablePending.command)
      : runtimeFromXingceSession(session));
  if (!retryablePending) {
    runtime.lastAutosaveElapsedSeconds = runtime.task.totalElapsedSeconds;
  }
  page.setData({
    hasPendingCheckpoint: retryablePending !== null,
    pendingCheckpointMessage: retryablePending
      ? "检测到一次结果未知的断点保存，已恢复当时的本地草稿；请按原命令重试。"
      : "",
    interactionLocked: retryablePending !== null,
  });
  render(page, runtime);
  if (session.state === "answering" && runtime.visible) startTimer(page, runtime);
  else stopTimer(runtime);
}

function render(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): void {
  if (!runtime.session || !runtime.task) return;
  const view = presentXingceTask(runtime.session, runtime.task);
  const currentQuestionId = runtime.task.questionOrder[runtime.task.currentQuestionIndex] ?? "";
  const currentAttempt = runtime.task.attempts[currentQuestionId];
  const defaultAnchor = view.question.material?.anchors[0]?.anchorId ?? "material-start";
  const materialPosition = currentAttempt?.materialPosition ?? {
    anchorId: defaultAnchor,
    scrollTop: 0,
    zoom: 1,
  };
  const materialReaderState =
    page.data.materialQuestionId === currentQuestionId
      ? {
          ...page.data.materialReaderState,
          position: materialPosition,
          preferences: page.data.displayPreferences,
        }
      : createMaterialReaderState(materialPosition, page.data.displayPreferences);
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
    view,
    result: presentXingceResult(runtime.session),
    sessionState: runtime.session.state,
    canSubmit: runtime.session.state === "answering" && missing.length === 0,
    answerSheetItems,
    materialQuestionId: currentQuestionId,
    materialOpen: materialReaderState.mode !== "question_first",
    materialReaderState,
    toolPolicy: taskToolPolicy(
      runtime.session.mode === "verification" ? "retest" : "targeted",
      runtime.session.state === "answering" ? "answering" : "submitted",
    ),
    helpReference: taskHelpReference(runtime.session),
    helpSyncState: runtime.task.syncState,
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
  const api = runtime.api;
  const ownerUserId = runtime.ownerUserId;
  const sessionId = runtime.session.sessionId;
  runtime.saveInFlight = true;
  stopTimer(runtime);
  runtime.task = reduceTaskRuntime(runtime.task, { type: "SAVE_REQUESTED" });
  render(page, runtime);
  page.setData({ interactionLocked: true });
  const localDraft = runtime.task;
  let commandConfirmed = false;
  let pending = loadPendingXingceCheckpoint(ownerUserId, sessionId);
  try {
    if (!pending) {
      const command = checkpointCommand(runtime.task);
      pending = {
        kind: "checkpoint",
        planTaskId: runtime.planTaskId,
        sessionId,
        idempotencyKey: await createIdempotencyKey("xingce-save"),
        command,
      };
      storePendingXingceCheckpoint(ownerUserId, pending);
    }
    await api.saveCheckpoint(
      pending.sessionId,
      pending.command,
      pending.idempotencyKey,
      requiredEditToken(runtime),
    );
    commandConfirmed = true;
    clearPendingXingceCheckpoint(ownerUserId, pending.sessionId);
    if (!runtimes.owns(page, runtime)) return;
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(page, runtime);
      return;
    }
    const current = await api.getSession(pending.sessionId);
    if (!runtimes.owns(page, runtime)) return;
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(page, runtime);
      return;
    }
    page.setData({ hasPendingCheckpoint: false, pendingCheckpointMessage: "" });
    applySession(page, runtime, current.data);
  } catch (error) {
    if (!runtimes.owns(page, runtime)) return;
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
    if (
      error instanceof ApiClientError &&
      error.statusCode === 409 &&
      conflictRetry &&
      error.payload?.code === "xingce_revision_conflict"
    ) {
      if (!pending) throw error;
      const ownership = runtime.editOwnership;
      const editingApi = runtime.editingApi;
      if (!ownership || !editingApi) throw error;
      const resolved = await resolveRevisionConflict(
        editingApi,
        ownership.lease,
        pending.command.expectedRevision,
      );
      runtime.editOwnership = resolved;
      const current = await api.getSession(sessionId);
      if (!runtimes.owns(page, runtime)) return;
      clearPendingXingceCheckpoint(ownerUserId, sessionId);
      if (resolved.conflictChoice === "use_server" || current.data.state !== "answering") {
        page.setData({ hasPendingCheckpoint: false, pendingCheckpointMessage: "" });
        applySession(page, runtime, current.data);
        return;
      }
      runtime.session = current.data;
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

async function acquireEditingForSession(
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
  session: XingceTaskSession,
): Promise<ReturnType<typeof runtimeFromXingceSession> | null> {
  if (session.state !== "answering") {
    runtime.editOwnership = null;
    return null;
  }
  if (!runtime.editingApi) throw new Error("设备编辑权服务没有初始化");
  const pending = loadPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
  const ownership = await acquireTaskEditOwnership(
    runtime.editingApi,
    "xingce_task_session",
    session.sessionId,
    pending?.command.expectedRevision ?? session.revision,
    pending !== null,
  );
  runtime.editOwnership = ownership;
  if (ownership.conflictChoice === "use_server") {
    clearPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
    return null;
  }
  if (ownership.conflictChoice === "use_this_device" && pending) {
    clearPendingXingceCheckpoint(runtime.ownerUserId, session.sessionId);
    const local = runtimeWithPendingCheckpoint(session, pending.command);
    storePendingXingceCheckpoint(runtime.ownerUserId, {
      ...pending,
      idempotencyKey: await createIdempotencyKey("xingce-save-resolved"),
      command: checkpointCommand(local),
    });
    return local;
  }
  return null;
}

function requiredEditToken(runtime: NonNullable<ReturnType<typeof runtimes.get>>): {
  leaseId: string;
  generation: number;
} {
  const token = runtime.editOwnership?.token;
  if (!token) throw new Error("当前设备尚未取得任务编辑权");
  return token;
}

async function autosaveCheckpoint(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
  options: { leaving?: boolean } = {},
): Promise<void> {
  if (!runtime.task) return;
  const decision = decideAutosave(runtime.task, {
    saveInFlight: runtime.saveInFlight,
    lastSavedElapsedSeconds: runtime.lastAutosaveElapsedSeconds,
    intervalSeconds: 15,
    leaving: options.leaving,
  });
  if (!decision.shouldSave) return;
  await saveCheckpointIfPossible(page, runtime);
}

function startTimer(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): void {
  stopTimer(runtime);
  if (!runtime.visible || page.data.taskPaused) return;
  runtime.timerHandle = setInterval(() => {
    if (
      !runtimes.owns(page, runtime) ||
      !runtime.visible ||
      !runtime.task ||
      runtime.task.phase !== "answering"
    )
      return;
    runtime.task = reduceTaskRuntime(runtime.task, { type: "TICK", seconds: 1 });
    render(page, runtime);
    void autosaveCheckpoint(page, runtime);
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

function updateDisplayPreferences(
  page: PracticePageInstance,
  preferences: ContentDisplayPreferences,
): void {
  const materialReaderState = {
    ...page.data.materialReaderState,
    preferences,
  };
  page.setData({ displayPreferences: preferences, materialReaderState });
  storeTaskDisplayPreferences(preferences);
}

function taskHelpReference(session: XingceTaskSession): TaskHelpPageObjectReference {
  return {
    pageCode: "C02",
    route: routes.practice,
    track: "xingce",
    objectType: "task_session",
    objectId: session.sessionId,
    taskMode: session.mode === "verification" ? "retest" : session.mode,
    lifecycle: session.state === "answering" ? "answering" : "submitted",
  };
}
