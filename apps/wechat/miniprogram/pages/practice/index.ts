import { ApiClient, ApiClientError } from "../../api/client";
import { CompanionApi } from "../../api/companion";
import { LearningBrainApi } from "../../api/learning-brain";
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
  XingcePageContractError,
} from "../../features/xingce/real_attempt/controller";
import {
  isValidPlanTaskId,
  parseXingceTaskMode,
} from "../../features/xingce/real_attempt/contracts";
import { XingcePageRuntimeRegistry } from "../../features/xingce/real_attempt/page-runtime";
import {
  presentXingceTask,
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
import {
  acquireTaskEditOwnership,
  resolveRevisionConflict,
  TaskEditTakeoverDeclined,
} from "../../features/task-editing/coordinator";
import { createIdempotencyKey } from "../../platform/command-id";
import { decideAutosave, decideExit } from "../../task-runtime/state/autosave";
import type { AnnotationStroke } from "../../task-runtime/contracts";
import { reduceTaskRuntime, unansweredQuestionIds } from "../../task-runtime/state/runtime";
import type {
  ContentAnchor,
  ContentDisplayPreferences,
  RichContentBlock,
} from "../../task-runtime/content-renderer/model";
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
  showTodayAction: boolean;
  view: XingceTaskView | null;
  sessionState: XingceTaskSession["state"] | "";
  materialOpen: boolean;
  materialQuestionId: string;
  materialReaderState: MaterialReaderState;
  displayPreferences: ContentDisplayPreferences;
  emptyRichContentBlocks: RichContentBlock[];
  emptyContentAnchors: ContentAnchor[];
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
  ananContextOpen: boolean;
  /** Figma 70:2：题面批注模式。 */
  annotationOpen: boolean;
  /** Figma 84:2：画词选择状态。 */
  selection: { key: string; text: string; toolbarTop: number };
  /** Figma 97:2：上下文追问岸岸。 */
  ananInput: string;
  ananAsking: boolean;
  ananReply: string;
  ananReplyTitle: string;
  ananError: string;
  learnerStateVersion: string;
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
    marked: boolean;
    active: boolean;
  }>;
  /** Figma 61:2：图例的真实计数。 */
  answerSheetLegend: {
    answered: number;
    unanswered: number;
    marked: number;
  };
  /** Figma 60:10：`资料分析 · 共 5 题`，来自真实 bundle。 */
  answerSheetSubtitle: string;
  /** Figma 61:16：模块行右侧 `2 已答 · 2 未答 · 1 当前`。 */
  answerSheetModuleName: string;
  answerSheetModuleCounts: string;
  confidenceOptions: Array<{ value: "low" | "medium" | "high"; label: string }>;
}

interface PracticePageCustom {
  loadTask(): Promise<void>;
  retry(): void;
  goLogin(): void;
  goToday(): void;
  exit(): Promise<void>;
  toggleMaterial(): void;
  updateMaterialReader(event: WechatMiniprogram.CustomEvent<{ state: MaterialReaderState }>): void;
  openTaskTools(): void;
  openFocusTool(): void;
  openAnanContext(): void;
  closeAnanContext(): void;
  openAnnotation(): void;
  closeAnnotation(): void;
  changeAnnotations(
    event: WechatMiniprogram.CustomEvent<{
      strokes: AnnotationStroke[];
      action: "added" | "erased";
    }>,
  ): void;
  tapStemSegment(
    event: WechatMiniprogram.CustomEvent<{
      key: string;
      text: string;
      rect: { top?: number; bottom?: number } | null;
    }>,
  ): void;
  explainSelection(): void;
  compareSelectionOptions(): void;
  askAboutSelection(): void;
  markSelectionOnQuestion(): void;
  logSelectionToDraft(): void;
  inputAnanQuestion(event: WechatMiniprogram.Input): void;
  sendAnanQuestion(): Promise<void>;
  askAnan(question: string): Promise<void>;
  closeTaskOverlay(): void;
  openTaskHelp(): void;
  submitTaskHelp(event: WechatMiniprogram.CustomEvent<TaskHelpCommand>): Promise<void>;
  openDispute(): void;
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
    showTodayAction: false,
    view: null,
    sessionState: "",
    materialOpen: false,
    materialQuestionId: "",
    materialReaderState: createMaterialReaderState(),
    displayPreferences: { fontScale: 1, colorMode: "standard" },
    emptyRichContentBlocks: [],
    emptyContentAnchors: [],
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
    ananContextOpen: false,
    annotationOpen: false,
    selection: { key: "", text: "", toolbarTop: 0 },
    ananInput: "",
    ananAsking: false,
    ananReply: "",
    ananReplyTitle: "",
    ananError: "",
    learnerStateVersion: "",
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
    answerSheetLegend: { answered: 0, unanswered: 0, marked: 0 },
    answerSheetSubtitle: "",
    answerSheetModuleName: "",
    answerSheetModuleCounts: "",
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
    const auth = currentAuthSession();
    if (!auth) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能开始行测任务",
        errorMessage: "作答进度和复盘记录必须绑定你的账号。",
        showLoginAction: true,
        showTodayAction: false,
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
      showTodayAction: false,
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
      if (
        error instanceof XingcePageContractError &&
        error.message === "当前今日计划没有与入口匹配的可执行行测任务。"
      ) {
        this.setData({
          status: "unavailable",
          errorTitle: "今天没有匹配的行测任务",
          errorMessage: "回到今日计划选择一项可执行的行测任务，再从任务卡进入。",
          showLoginAction: false,
          showTodayAction: true,
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

  goToday() {
    void wx.switchTab({ url: routes.today });
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
    // Figma 80:2：材料标题行的展开入口直接进入沉浸阅读，全屏态承载全部阅读工具。
    const event =
      this.data.materialReaderState.mode === "question_first"
        ? ({ type: "OPEN_FULLSCREEN" } as const)
        : ({ type: "RETURN_TO_QUESTION" } as const);
    const materialReaderState = reduceMaterialReader(this.data.materialReaderState, event);
    this.setData({
      materialOpen: materialReaderState.mode !== "question_first",
      materialReaderState,
    });
    const runtime = runtimes.get(this);
    if (runtime?.task && event.type === "OPEN_FULLSCREEN") {
      runtime.task = reduceTaskRuntime(runtime.task, {
        type: "ACTION_LOGGED",
        action: "material_expanded",
      });
    }
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

  // Figma 97:2：材料标题行的岸岸入口打开当前题目的上下文追问面板。
  openAnanContext() {
    const runtime = runtimes.get(this);
    if (!runtime?.session || runtime.session.state !== "answering") return;
    this.setData({
      ananContextOpen: true,
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
      draftOpen: false,
      answerSheetOpen: false,
    });
  },

  closeAnanContext() {
    this.setData({ ananContextOpen: false, ananAsking: false });
  },

  // Figma 70:2：进入题面批注模式（笔迹随检查点自动保存）。
  openAnnotation() {
    const runtime = runtimes.get(this);
    if (!runtime?.session || runtime.session.state !== "answering") return;
    this.setData({
      annotationOpen: true,
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
      draftOpen: false,
      answerSheetOpen: false,
      ananContextOpen: false,
      selection: { key: "", text: "", toolbarTop: 0 },
    });
  },

  closeAnnotation() {
    this.setData({ annotationOpen: false });
  },

  // 笔迹层提交（新增或擦除都是完整替换），进入 runtime 并触发自动保存。
  changeAnnotations(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.task || this.data.interactionLocked) return;
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "ANNOTATIONS_CHANGED",
      strokes: event.detail.strokes,
      action: event.detail.action,
    });
    render(this, runtime);
    void autosaveCheckpoint(this, runtime);
  },

  // Figma 84:2 画词：选中句段并把工具条放在选区下方（避开选中文字与固定底栏）。
  tapStemSegment(event) {
    const { key, text, rect } = event.detail;
    if (this.data.selection.key === key) {
      this.setData({ selection: { key: "", text: "", toolbarTop: 0 } });
      return;
    }
    const windowHeight = wx.getWindowInfo().windowHeight;
    const preferred = (rect?.bottom ?? 0) + 10;
    const toolbarTop = Math.min(preferred, windowHeight - 180);
    this.setData({ selection: { key, text, toolbarTop } });
  },

  explainSelection() {
    const text = this.data.selection.text.trim();
    if (!text) return;
    this.openAnanContext();
    void this.askAnan(`请解释这句话在本题里的含义：${text}`);
  },

  compareSelectionOptions() {
    const view = this.data.view;
    if (!view) return;
    const labels = view.question.options.map((option) => option.label).join("/");
    this.openAnanContext();
    void this.askAnan(`请对比本题选项 ${labels} 的差异，以及排查它们时应该先看什么。`);
  },

  askAboutSelection() {
    this.openAnanContext();
  },

  // Figma 97:191：把选中文字用荧光笔标在题面上（真实批注，不是演示）。
  markSelectionOnQuestion() {
    const runtime = runtimes.get(this);
    const text = this.data.selection.text.trim();
    const view = this.data.view;
    if (!runtime?.task || !text || !view) return;
    const query = this.createSelectorQuery();
    query.select(".practice-workspace").boundingClientRect();
    query.exec(
      (
        results: Array<{
          top?: number;
          left?: number;
          width?: number;
          height?: number;
        } | null>,
      ) => {
        const workspace = results[0];
        if (!runtime.task || !workspace?.width || !workspace.height) return;
        const top = this.data.selection.toolbarTop - 10;
        const normalizedY = Math.min(
          0.98,
          Math.max(0.02, (top - (workspace.top ?? 0) - 12) / workspace.height),
        );
        const stroke: AnnotationStroke = {
          strokeId: `mark-${String(runtime.task.totalElapsedSeconds)}-${Math.random().toString(36).slice(2, 8)}`,
          tool: "highlighter",
          width: 0.03,
          points: [
            { x: 0.08, y: normalizedY },
            { x: 0.92, y: normalizedY },
          ],
          atMs:
            (runtime.task.attempts[
              runtime.task.questionOrder[runtime.task.currentQuestionIndex] ?? ""
            ]?.elapsedSeconds ?? 0) * 1000,
        };
        runtime.task = reduceTaskRuntime(runtime.task, {
          type: "ANNOTATIONS_CHANGED",
          strokes: [...view.question.annotations, stroke],
          action: "added",
        });
        render(this, runtime);
        void autosaveCheckpoint(this, runtime);
        this.setData({ ananContextOpen: false });
        void wx.showToast({ title: "已在题面标出", icon: "none" });
      },
    );
  },

  // Figma 97:195：把选中文字与岸岸回复记入本题草稿（真实持久化的错因线索）。
  logSelectionToDraft() {
    const runtime = runtimes.get(this);
    const view = this.data.view;
    if (!runtime?.task || !view || this.data.interactionLocked) return;
    const parts = [
      view.question.draftText.trim(),
      this.data.selection.text ? `【存疑】${this.data.selection.text}` : "",
      this.data.ananReply ? `【岸岸】${this.data.ananReply}` : "",
    ].filter(Boolean);
    if (parts.length === 0) return;
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "DRAFT_CHANGED",
      text: parts.join("\n").slice(0, 4000),
    });
    render(this, runtime);
    void autosaveCheckpoint(this, runtime);
    void wx.showToast({ title: "已记入本题草稿", icon: "none" });
  },

  inputAnanQuestion(event) {
    this.setData({ ananInput: event.detail.value });
  },

  async sendAnanQuestion() {
    const question = this.data.ananInput.trim();
    if (question.length < 4) {
      this.setData({ ananError: "请至少写 4 个字，让岸岸知道你在问什么。" });
      return;
    }
    this.setData({ ananInput: "" });
    await this.askAnan(question);
  },

  // Figma 97:2：走正式的岸岸问答接口；答题期的边界由后端治理层裁决。
  async askAnan(question) {
    const runtime = runtimes.get(this);
    const auth = currentAuthSession();
    const view = this.data.view;
    if (!runtime?.session || !auth || !view || this.data.ananAsking) return;
    this.setData({ ananAsking: true, ananError: "", ananReply: "", ananReplyTitle: "" });
    try {
      const client = new ApiClient(environment.apiBaseUrl, auth.accessToken);
      let learnerStateVersion = this.data.learnerStateVersion;
      if (!learnerStateVersion) {
        const { data: profile } = await new LearningBrainApi(client).getProfile();
        learnerStateVersion = profile.profileVersionId;
        this.setData({ learnerStateVersion });
      }
      const material = view.question.material;
      const quoted = this.data.selection.text.trim();
      const { data } = await new CompanionApi(client).ask(
        {
          questionText: quoted ? `${question}（题干选中内容：${quoted}）` : question,
          context: {
            routeId: "pages/practice/index",
            workspace: "training",
            objectRefs: [
              {
                type: "question",
                id: view.question.questionId,
                version: view.question.contentVersionId,
              },
              ...(material
                ? [
                    {
                      type: "material" as const,
                      id: material.materialId,
                      version: view.question.contentVersionId,
                    },
                  ]
                : []),
            ],
            taskMode: runtime.session.mode === "verification" ? "retest" : "practice",
            taskStage: "answering",
            learnerStateVersion,
          },
        },
        await createIdempotencyKey("practice-anan"),
      );
      this.setData({
        ananAsking: false,
        ananReply: data.answer,
        ananReplyTitle: data.companionIntent === "reminder" ? "先等一下。" : "",
      });
    } catch (error) {
      this.setData({
        ananAsking: false,
        ananError:
          error instanceof ApiClientError ? error.message : "岸岸暂时没有回应，请稍后再试。",
      });
    }
  },

  closeTaskOverlay() {
    this.setData({
      toolsOpen: false,
      helpOpen: false,
      disputeOpen: false,
      draftOpen: false,
      ananContextOpen: false,
    });
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
      await runtime.api.reportDispute(
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
        disputeReceipt: "已记录，我们会按这道题继续核对。",
        disputeDescription: "",
      });
      if (runtime.session.state !== "answering") {
        const refreshed = await runtime.api.getSession(runtime.session.sessionId);
        if (!runtimes.owns(this, runtime) || !currentAccountOwns(runtime)) return;
        applySession(this, runtime, refreshed.data);
        this.setData({
          disputeOpen: true,
          disputeReceipt: "已记录；这次复测结果已暂停参与能力判断。",
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
    // Figma 78:2：暂停/继续是可见的作答动作事实。
    runtime.task = reduceTaskRuntime(runtime.task, {
      type: "ACTION_LOGGED",
      action: taskPaused ? "paused" : "resumed",
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
      ? "检测到一次结果未知的进度保存，已恢复当时的本地草稿；请重试刚才那次保存。"
      : "",
    interactionLocked: retryablePending !== null,
  });
  if (session.state !== "answering") {
    stopTimer(runtime);
    redirectToReflection(page, runtime);
    return;
  }
  render(page, runtime);
  if (runtime.visible) startTimer(page, runtime);
  else stopTimer(runtime);
}

function redirectToReflection(
  page: PracticePageInstance,
  runtime: NonNullable<ReturnType<typeof runtimes.get>>,
): void {
  const session = runtime.session;
  const reviewId = session?.review?.reviewId;
  if (!session || !reviewId) {
    failPage(page, "复盘记录尚未生成", "交卷已经完成，但复盘记录还不可用。请重新读取本次训练。");
    return;
  }
  if (runtime.reflectionNavigationStarted) return;
  runtime.reflectionNavigationStarted = true;
  page.setData({
    status: "loading",
    errorTitle: "",
    errorMessage: "",
    sessionState: session.state,
    interactionLocked: true,
  });
  const url =
    routes.reflection +
    "?sessionId=" +
    encodeURIComponent(session.sessionId) +
    "&reviewId=" +
    encodeURIComponent(reviewId);
  void wx.redirectTo({ url }).catch(() => {
    if (!runtimes.owns(page, runtime)) return;
    runtime.reflectionNavigationStarted = false;
    failPage(page, "复盘页面没有打开", "请重新读取本次训练，再进入训练总结与错因复盘。");
  });
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
      marked: attempt?.marked ?? false,
      active: index === runtime.task?.currentQuestionIndex,
    };
  });
  // Figma 60:4 答题卡：图例、模块行与题号圆点全部来自真实作答状态。
  const answeredCount = answerSheetItems.filter((item) => item.state === "answered").length;
  const answerSheetLegend = {
    answered: answeredCount,
    unanswered: answerSheetItems.length - answeredCount,
    marked: answerSheetItems.filter((item) => item.marked).length,
  };
  const answerSheetModuleName = view.question.moduleName;
  const answerSheetSubtitle = `${answerSheetModuleName} · 共 ${String(answerSheetItems.length)} 题`;
  const answerSheetModuleCounts = `${String(answerSheetLegend.answered)} 已答 · ${String(answerSheetLegend.unanswered)} 未答 · 1 当前`;
  const alreadyReady = page.data.status === "ready";
  page.setData({
    ...(alreadyReady ? { status: "ready" as const } : {}),
    errorTitle: "",
    errorMessage: "",
    view,
    sessionState: runtime.session.state,
    canSubmit: runtime.session.state === "answering" && missing.length === 0,
    answerSheetItems,
    answerSheetLegend,
    answerSheetSubtitle,
    answerSheetModuleName,
    answerSheetModuleCounts,
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
  if (!alreadyReady) {
    afterRenderTick(() => {
      if (runtimes.owns(page, runtime)) page.setData({ status: "ready" });
    });
  }
}

function afterRenderTick(callback: () => void): void {
  if (typeof wx.nextTick === "function") {
    wx.nextTick(callback);
    return;
  }
  void Promise.resolve().then(callback);
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
        errorTitle: "进度已保存，但页面刷新失败",
        errorMessage: "刚才的保存已确认；请重新加载当前作答后再继续。",
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
    handleInlineError(
      page,
      runtime,
      error,
      "进度还没确认成功，本机会保留刚才那次保存，方便你重试。",
    );
    page.setData({
      hasPendingCheckpoint: true,
      pendingCheckpointMessage: "这次保存还没确认成功，需要重试后才能继续修改。",
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
  return error instanceof Error ? error.message : "没有拿到可确认的最新状态。";
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
    errorMessage: "旧账号的题目和进度已从当前页面清除，请以当前账号重新进入今日计划。",
    showLoginAction: true,
    view: null,
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
