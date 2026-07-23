import { authedRequest, authedV2, idemKey } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import { config } from "../../config/env";
import type {
  MockExamCheckpointReceipt,
  MockExamCheckpointRequest,
  MockExamSession,
  MockExamSubmitRequest,
  MockExamSubmitResponse,
  MockExamTrajectoryEventCommand,
  QuestionDisputeReceipt,
  ReportQuestionDisputeCommand,
  SafeOption,
} from "../../api/generated/types.gen";
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
  applyPendingCheckpoint,
  applyPendingSubmit,
  buildCheckpointCommand,
  buildSubmitCommand,
  countAnswers,
  formatRemainingTime,
  rebaseLocalOntoSession,
  remainingSeconds,
  requiredAttempt,
  runtimeFromSession,
  withServerRevision,
  type MockAttempt,
  type MockRuntimeState,
} from "./runtime";

type ViewState = "loading" | "error" | "answering" | "submitted";
type DisputeCategory = ReportQuestionDisputeCommand["category"];
type TrajectoryPayload = Record<string, string | number | boolean | null>;

interface OptionView extends SafeOption {
  selected: boolean;
  eliminated: boolean;
}

/** 材料视图：文本可折叠，图片直接铺出 */
interface MaterialView {
  title: string;
  text: string;
  images: Array<{ url: string; altText: string }>;
  collapsible: boolean;
  collapsed: boolean;
}

interface SheetCell {
  index: number;
  seq: number;
  answered: boolean;
  marked: boolean;
  current: boolean;
}

/** 交卷后的聚合结果视图（无解析，解析只在报告页解锁） */
interface SubmittedView {
  hasReceipt: boolean;
  performanceIndex: number;
  rangeLower: number;
  rangeUpper: number;
  estimatedScore: number;
  metricDefinition: string;
  submittedAtLabel: string;
}

interface MockExamPageData {
  statusBarHeight: number;
  viewState: ViewState;
  errorTitle: string;
  errorDesc: string;
  requestId: string;
  inlineError: string;
  title: string;
  currentIndex: number;
  totalCount: number;
  moduleName: string;
  remainingText: string;
  syncLabel: string;
  stem: string;
  options: OptionView[];
  material: MaterialView | null;
  currentMarked: boolean;
  currentDraft: string;
  isFirst: boolean;
  isLast: boolean;
  answeredCount: number;
  unansweredCount: number;
  markedCount: number;
  interactionLocked: boolean;
  submitting: boolean;
  sheetOpen: boolean;
  sheetCells: SheetCell[];
  toolsOpen: boolean;
  draftOpen: boolean;
  disputeOpen: boolean;
  disputeCategories: Array<{ value: DisputeCategory; label: string }>;
  disputeCategory: DisputeCategory;
  disputeDescription: string;
  disputeStatus: "idle" | "submitting" | "resolved" | "error";
  disputeMessage: string;
  submitted: SubmittedView | null;
  reportSessionId: string;
}

interface PageRuntime {
  ownerId: string;
  requestedSessionId: string;
  session: MockExamSession | null;
  state: MockRuntimeState | null;
  dirty: Set<string>;
  mutationVersion: number;
  saving: Promise<boolean> | null;
  trajectoryFlush: Promise<boolean> | null;
  nextEventSeq: number;
  submitInFlight: boolean;
  visible: boolean;
  disposed: boolean;
  timerId: ReturnType<typeof setInterval> | null;
  saveTimer: ReturnType<typeof setTimeout> | null;
}

interface MockExamPageCustom {
  loadExam(): Promise<void>;
  retry(): void;
  exit(): Promise<void>;
  selectOption(event: WechatMiniprogram.BaseEvent): void;
  toggleEliminated(event: WechatMiniprogram.BaseEvent): void;
  toggleMarked(): void;
  toggleMaterial(): void;
  inputDraft(event: WechatMiniprogram.Input): void;
  previousQuestion(): void;
  nextQuestion(): void;
  jumpTo(event: WechatMiniprogram.BaseEvent): void;
  openSheet(): void;
  closeSheet(): void;
  toggleTools(): void;
  toggleDraft(): void;
  openDispute(): void;
  closeDispute(): void;
  selectDisputeCategory(event: WechatMiniprogram.BaseEvent): void;
  inputDisputeDescription(event: WechatMiniprogram.Input): void;
  submitDispute(): Promise<void>;
  submitExam(): Promise<void>;
  openMockReport(): void;
  returnToday(): void;
}

type MockExamPageInstance = WechatMiniprogram.Page.Instance<MockExamPageData, MockExamPageCustom>;

const runtimes = new WeakMap<MockExamPageInstance, PageRuntime>();
const MATERIAL_COLLAPSE_CHARS = 150;
const DISPUTE_CATEGORIES: Array<{ value: DisputeCategory; label: string }> = [
  { value: "stem_error", label: "题干错误" },
  { value: "option_error", label: "选项错误" },
  { value: "answer_error", label: "答案争议" },
  { value: "material_mismatch", label: "材料不匹配" },
  { value: "image_unreadable", label: "图片无法辨认" },
  { value: "other", label: "其他问题" },
];

Page<MockExamPageData, MockExamPageCustom>({
  data: {
    statusBarHeight: 20,
    viewState: "loading",
    errorTitle: "",
    errorDesc: "",
    requestId: "",
    inlineError: "",
    title: "每周整卷模考",
    currentIndex: 0,
    totalCount: 0,
    moduleName: "",
    remainingText: "--:--:--",
    syncLabel: "正在连接",
    stem: "",
    options: [],
    material: null,
    currentMarked: false,
    currentDraft: "",
    isFirst: true,
    isLast: false,
    answeredCount: 0,
    unansweredCount: 0,
    markedCount: 0,
    interactionLocked: false,
    submitting: false,
    sheetOpen: false,
    sheetCells: [],
    toolsOpen: false,
    draftOpen: false,
    disputeOpen: false,
    disputeCategories: DISPUTE_CATEGORIES,
    disputeCategory: "stem_error",
    disputeDescription: "",
    disputeStatus: "idle",
    disputeMessage: "",
    submitted: null,
    reportSessionId: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    runtimes.set(this, {
      ownerId: ownerKey(),
      requestedSessionId: options.sessionId?.trim() ?? "",
      session: null,
      state: null,
      dirty: new Set<string>(),
      mutationVersion: 0,
      saving: null,
      trajectoryFlush: null,
      nextEventSeq: 1,
      submitInFlight: false,
      visible: false,
      disposed: false,
      timerId: null,
      saveTimer: null,
    });
    void this.loadExam();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    runtime.visible = true;
    if (runtime.session && runtime.state && this.data.viewState === "answering") {
      refreshClock(this, runtime);
      startClock(this, runtime);
      queueTrajectory(runtime, "resumed", currentQuestionId(runtime), {
        remainingSeconds: remainingSeconds(runtime.session.deadlineAt, Date.now()),
      });
    }
  },

  onHide() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    runtime.visible = false;
    stopClock(runtime);
    clearAutosave(runtime);
    if (runtime.session && runtime.state && this.data.viewState === "answering") {
      queueTrajectory(runtime, "left", currentQuestionId(runtime), {
        answeredCount: countAnswers(runtime.state).answered,
      });
      void saveCheckpoint(this, runtime);
    }
  },

  onUnload() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    runtime.disposed = true;
    stopClock(runtime);
    clearAutosave(runtime);
    runtimes.delete(this);
  },

  // ═══════ 加载会话（周模考创建/恢复 + 断点续做） ═══════

  async loadExam() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    this.setData({ viewState: "loading", errorTitle: "", errorDesc: "", requestId: "" });
    try {
      const session = runtime.requestedSessionId
        ? await apiGetSession(runtime.requestedSessionId)
        : await apiCreateWeekly();
      if (runtimes.get(this) !== runtime) return;
      if (session.status !== "assigned") {
        runtime.session = session;
        runtime.state = null;
        this.setData({
          viewState: "submitted",
          title: session.title,
          reportSessionId: session.sessionId,
          interactionLocked: true,
          submitted: emptySubmittedView(),
        });
        return;
      }
      runtime.session = session;
      runtime.state = runtimeFromSession(session);
      runtime.dirty = new Set<string>();
      initializeTrajectory(runtime, session);
      applyRuntimeToPage(this, runtime, "已恢复上次进度");
      queueTrajectory(runtime, "visited", currentQuestionId(runtime), { entry: "session_load" });

      // 先恢复未确认结果的交卷命令，再恢复未确认结果的 checkpoint
      const pendingSubmit = loadPendingMockSubmit(runtime.ownerId, session.sessionId);
      if (pendingSubmit) {
        await resumePendingSubmit(this, runtime, pendingSubmit);
        return;
      }
      const pendingCheckpoint = loadPendingMockCheckpoint(runtime.ownerId, session.sessionId);
      if (pendingCheckpoint && runtime.state) {
        runtime.state = applyPendingCheckpoint(runtime.state, pendingCheckpoint.command);
        runtime.dirty = new Set(
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
      failPage(this, error, "整卷恢复失败");
    }
  },

  retry() {
    void this.loadExam();
  },

  async exit() {
    const runtime = runtimes.get(this);
    if (!runtime?.state || !runtime.session) {
      wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
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
    if (result.confirm) {
      wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
    }
  },

  // ═══════ 作答交互 ═══════

  selectOption(event) {
    const runtime = requiredRuntime(this);
    if (this.data.interactionLocked) return;
    const optionId = dataset(event, "optionid");
    const questionId = currentQuestionId(runtime);
    const attempt = requiredAttempt(requiredState(runtime), questionId);
    // 单选：重复点已选项视为撤销
    const selectedAnswer = attempt.selectedAnswer === optionId ? null : optionId;
    updateAttempt(runtime, questionId, { selectedAnswer });
    queueTrajectory(runtime, "answer_changed", questionId, { selectedOptionId: optionId });
    applyRuntimeToPage(this, runtime, "待保存");
    scheduleAutosave(this, runtime);
  },

  toggleEliminated(event) {
    const runtime = requiredRuntime(this);
    if (this.data.interactionLocked) return;
    const optionId = dataset(event, "optionid");
    const questionId = currentQuestionId(runtime);
    const attempt = requiredAttempt(requiredState(runtime), questionId);
    const eliminatedOptionIds = attempt.eliminatedOptionIds.includes(optionId)
      ? attempt.eliminatedOptionIds.filter((item) => item !== optionId)
      : [...attempt.eliminatedOptionIds, optionId];
    updateAttempt(runtime, questionId, { eliminatedOptionIds });
    queueTrajectory(runtime, "answer_changed", questionId, {
      interaction: "option_elimination",
      optionId,
    });
    applyRuntimeToPage(this, runtime, "待保存");
    scheduleAutosave(this, runtime);
  },

  toggleMarked() {
    const runtime = requiredRuntime(this);
    if (this.data.interactionLocked) return;
    const questionId = currentQuestionId(runtime);
    const marked = !requiredAttempt(requiredState(runtime), questionId).marked;
    updateAttempt(runtime, questionId, { marked });
    queueTrajectory(runtime, "mark_changed", questionId, { marked });
    applyRuntimeToPage(this, runtime, "待保存");
    scheduleAutosave(this, runtime);
  },

  inputDraft(event) {
    const runtime = requiredRuntime(this);
    if (this.data.interactionLocked) return;
    const questionId = currentQuestionId(runtime);
    updateAttempt(runtime, questionId, { draftText: event.detail.value });
    this.setData({ currentDraft: event.detail.value, syncLabel: "待保存" });
    scheduleAutosave(this, runtime);
  },

  toggleMaterial() {
    const material = this.data.material;
    if (!material || !material.collapsible) return;
    this.setData({ material: { ...material, collapsed: !material.collapsed } });
  },

  previousQuestion() {
    moveQuestion(this, -1);
  },

  nextQuestion() {
    moveQuestion(this, 1);
  },

  jumpTo(event) {
    const runtime = requiredRuntime(this);
    const index = Number(dataset(event, "index"));
    this.setData({ sheetOpen: false });
    goToQuestion(this, runtime, index, "answer_sheet");
  },

  // ═══════ 弹层 ═══════

  openSheet() {
    const runtime = requiredRuntime(this);
    const state = requiredState(runtime);
    this.setData({
      sheetOpen: true,
      toolsOpen: false,
      draftOpen: false,
      sheetCells: state.questionOrder.map((questionId, index) => {
        const attempt = requiredAttempt(state, questionId);
        return {
          index,
          seq: index + 1,
          answered: attempt.selectedAnswer !== null,
          marked: attempt.marked,
          current: index === state.currentQuestionIndex,
        };
      }),
    });
  },

  closeSheet() {
    this.setData({ sheetOpen: false });
  },

  toggleTools() {
    this.setData({ toolsOpen: !this.data.toolsOpen, sheetOpen: false, draftOpen: false });
  },

  toggleDraft() {
    if (this.data.viewState !== "answering") return;
    this.setData({
      draftOpen: !this.data.draftOpen,
      toolsOpen: false,
      sheetOpen: false,
    });
  },

  // ═══════ 题面争议 ═══════

  openDispute() {
    this.setData({
      disputeOpen: true,
      toolsOpen: false,
      disputeStatus: "idle",
      disputeMessage: "",
      disputeDescription: "",
    });
  },

  closeDispute() {
    if (this.data.disputeStatus === "submitting") return;
    this.setData({ disputeOpen: false });
  },

  selectDisputeCategory(event) {
    const category = dataset(event, "category");
    if (!DISPUTE_CATEGORIES.some((item) => item.value === category)) return;
    this.setData({ disputeCategory: category as DisputeCategory });
  },

  inputDisputeDescription(event) {
    this.setData({ disputeDescription: event.detail.value });
  },

  async submitDispute() {
    const runtime = requiredRuntime(this);
    const session = requiredSession(runtime);
    const state = requiredState(runtime);
    const question = session.questions[state.currentQuestionIndex];
    const description = this.data.disputeDescription.trim();
    if (!question || description.length < 2) {
      this.setData({ disputeStatus: "error", disputeMessage: "请用至少两个字说明争议。" });
      return;
    }
    this.setData({ disputeStatus: "submitting", disputeMessage: "正在绑定本场固定题面证据……" });
    try {
      const receipt = await apiReportDispute(
        session.sessionId,
        {
          questionId: question.questionId,
          contentVersionId: question.contentVersionId,
          category: this.data.disputeCategory,
          description,
        },
        idemKey(),
      );
      if (runtimes.get(this) !== runtime) return;
      this.setData({
        disputeStatus: "resolved",
        disputeMessage: `争议已记录 · ${receipt.disputeId}`,
      });
    } catch (error) {
      if (runtimes.get(this) !== runtime) return;
      this.setData({
        disputeStatus: "error",
        disputeMessage: errorMessage(error, "争议没有提交成功，请保留当前页面后重试。"),
      });
    }
  },

  // ═══════ 交卷 ═══════

  async submitExam() {
    const runtime = requiredRuntime(this);
    const counts = countAnswers(requiredState(runtime));
    const result = await wx.showModal({
      title: "确认交卷",
      content:
        counts.unanswered > 0
          ? `还有 ${counts.unanswered} 题未答。交卷后不能继续修改，是否提交？`
          : "交卷后不能继续修改，是否提交？",
      confirmText: "确认交卷",
      cancelText: "继续检查",
    });
    if (result.confirm) await executeSubmit(this, runtime);
  },

  openMockReport() {
    const sessionId = this.data.reportSessionId.trim();
    if (!sessionId) return;
    wx.navigateTo({
      url: `/pages/mock-report/index?sessionId=${encodeURIComponent(sessionId)}`,
    });
  },

  returnToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },
});

// ═══════ 状态变更与渲染 ═══════

function updateAttempt(
  runtime: PageRuntime,
  questionId: string,
  patch: Partial<Omit<MockAttempt, "questionId">>,
): void {
  const state = requiredState(runtime);
  const current = requiredAttempt(state, questionId);
  state.attempts = { ...state.attempts, [questionId]: { ...current, ...patch } };
  runtime.dirty.add(questionId);
  runtime.mutationVersion += 1;
}

function moveQuestion(page: MockExamPageInstance, delta: number): void {
  const runtime = requiredRuntime(page);
  const state = requiredState(runtime);
  goToQuestion(page, runtime, state.currentQuestionIndex + delta, delta > 0 ? "next" : "prev");
}

function goToQuestion(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  index: number,
  source: string,
): void {
  const state = requiredState(runtime);
  if (index < 0 || index >= state.questionOrder.length || index === state.currentQuestionIndex) {
    return;
  }
  const previousQuestionId = currentQuestionId(runtime);
  const skipped = requiredAttempt(state, previousQuestionId).selectedAnswer === null;
  // 离开的题目也要上行（停留位置对服务端有意义）
  runtime.dirty.add(previousQuestionId);
  state.currentQuestionIndex = index;
  runtime.dirty.add(currentQuestionId(runtime));
  runtime.mutationVersion += 1;
  applyRuntimeToPage(page, runtime, "待保存");
  scheduleAutosave(page, runtime);
  if (skipped) queueTrajectory(runtime, "skipped", previousQuestionId, { destinationIndex: index });
  queueTrajectory(runtime, "visited", currentQuestionId(runtime), { source });
}

function applyRuntimeToPage(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  syncLabel: string,
): void {
  const session = requiredSession(runtime);
  const state = requiredState(runtime);
  const question = session.questions[state.currentQuestionIndex];
  if (!question) throw new Error("当前题不在整卷内");
  const attempt = requiredAttempt(state, question.questionId);
  const material = question.materialId
    ? (session.materials.find((item) => item.materialId === question.materialId) ?? null)
    : null;
  const counts = countAnswers(state);
  page.setData({
    viewState: "answering",
    title: session.title,
    reportSessionId: session.sessionId,
    currentIndex: state.currentQuestionIndex,
    totalCount: state.questionOrder.length,
    moduleName: question.moduleName,
    stem: question.stem,
    options: question.options.map((option) => ({
      ...option,
      selected: attempt.selectedAnswer === option.optionId,
      eliminated: attempt.eliminatedOptionIds.includes(option.optionId),
    })),
    material: buildMaterialView(material, page.data.material),
    currentMarked: attempt.marked,
    currentDraft: attempt.draftText,
    isFirst: state.currentQuestionIndex === 0,
    isLast: state.currentQuestionIndex === state.questionOrder.length - 1,
    answeredCount: counts.answered,
    unansweredCount: counts.unanswered,
    markedCount: counts.marked,
    syncLabel,
  });
}

function buildMaterialView(
  material: MockExamSession["materials"][number] | null,
  previous: MaterialView | null,
): MaterialView | null {
  if (!material) return null;
  const text = material.text ?? "";
  const collapsible = text.length > MATERIAL_COLLAPSE_CHARS;
  // 同一题内保留用户的展开/收起选择
  const collapsed =
    previous && previous.title === (material.title ?? "") ? previous.collapsed : collapsible;
  return {
    title: material.title ?? "",
    text,
    images: material.assets.map((asset) => ({ url: asset.url, altText: asset.altText })),
    collapsible,
    collapsed,
  };
}

// ═══════ checkpoint 保存（含 409 版本冲突自愈） ═══════

function scheduleAutosave(page: MockExamPageInstance, runtime: PageRuntime): void {
  clearAutosave(runtime);
  runtime.saveTimer = setTimeout(() => {
    runtime.saveTimer = null;
    void saveCheckpoint(page, runtime);
  }, 800);
}

function clearAutosave(runtime: PageRuntime): void {
  if (runtime.saveTimer === null) return;
  clearTimeout(runtime.saveTimer);
  runtime.saveTimer = null;
}

async function saveCheckpoint(page: MockExamPageInstance, runtime: PageRuntime): Promise<boolean> {
  if (runtime.saving) return runtime.saving;
  const state = runtime.state;
  const session = runtime.session;
  if (!state || !session || runtime.dirty.size === 0) return true;
  const operation = performCheckpointSave(page, runtime);
  runtime.saving = operation;
  try {
    return await operation;
  } finally {
    if (runtime.saving === operation) runtime.saving = null;
  }
}

async function performCheckpointSave(
  page: MockExamPageInstance,
  runtime: PageRuntime,
): Promise<boolean> {
  const session = requiredSession(runtime);
  const state = requiredState(runtime);
  const sentQuestionIds = new Set(runtime.dirty);
  const command = buildCheckpointCommand(state, sentQuestionIds);
  const sentMutationVersion = runtime.mutationVersion;
  const idempotencyKey = idemKey();
  const pending: PendingMockCheckpoint = {
    kind: "checkpoint",
    ownerUserId: runtime.ownerId,
    sessionId: session.sessionId,
    idempotencyKey,
    command,
    storedAt: new Date().toISOString(),
  };
  storePendingMockCheckpoint(pending);
  page.setData({ syncLabel: "正在保存" });
  try {
    const receipt = await apiSaveCheckpoint(session.sessionId, command, idempotencyKey);
    clearPendingMockCheckpoint(runtime.ownerId, session.sessionId);
    if (runtimes.get(page) !== runtime) return true;
    runtime.session = sessionWithReceiptRevision(session, receipt);
    runtime.state = withServerRevision(requiredState(runtime), receipt.revision);
    if (runtime.mutationVersion === sentMutationVersion) {
      sentQuestionIds.forEach((questionId) => runtime.dirty.delete(questionId));
      applyRuntimeToPage(page, runtime, "已保存");
    } else {
      applyRuntimeToPage(page, runtime, "有新修改待保存");
      scheduleAutosave(page, runtime);
    }
    return true;
  } catch (error) {
    if (runtimes.get(page) !== runtime) return false;
    if (isConflict(error)) {
      return resolveCheckpointConflict(page, runtime);
    }
    page.setData({
      syncLabel: "保存失败",
      inlineError: errorMessage(error, "进度没有保存成功，将在恢复网络后重试。"),
    });
    return false;
  }
}

/** 409：以最新会话为基线保留本机作答，整卷标脏后重存 */
async function resolveCheckpointConflict(
  page: MockExamPageInstance,
  runtime: PageRuntime,
): Promise<boolean> {
  try {
    const latest = await apiGetSession(requiredSession(runtime).sessionId);
    if (runtimes.get(page) !== runtime) return false;
    clearPendingMockCheckpoint(runtime.ownerId, latest.sessionId);
    if (latest.status !== "assigned") {
      terminateAsSubmitted(page, runtime, latest, "云端已结束本场模考，本机草稿不再写入。");
      return true;
    }
    runtime.session = latest;
    runtime.state = rebaseLocalOntoSession(requiredState(runtime), latest);
    runtime.dirty = new Set(runtime.state.questionOrder);
    runtime.mutationVersion += 1;
    applyRuntimeToPage(page, runtime, "已合并云端版本，正在重新保存");
    return saveCheckpoint(page, runtime);
  } catch (error) {
    if (runtimes.get(page) !== runtime) return false;
    page.setData({
      syncLabel: "冲突尚未解决",
      inlineError: errorMessage(error, "版本冲突没有解决，请保持网络后重试。"),
    });
    return false;
  }
}

/** 恢复上次结果未知的 checkpoint：同一幂等键继续确认 */
async function resumePendingCheckpoint(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  pending: PendingMockCheckpoint,
): Promise<void> {
  if (runtime.saving) return;
  const operation = (async (): Promise<boolean> => {
    try {
      const receipt = await apiSaveCheckpoint(
        pending.sessionId,
        pending.command,
        pending.idempotencyKey,
      );
      clearPendingMockCheckpoint(runtime.ownerId, pending.sessionId);
      if (runtime.disposed) return true;
      runtime.session = sessionWithReceiptRevision(requiredSession(runtime), receipt);
      runtime.state = withServerRevision(requiredState(runtime), receipt.revision);
      pending.command.answers.forEach((answer) => runtime.dirty.delete(answer.questionId));
      applyRuntimeToPage(page, runtime, "已确认上次进度");
      return true;
    } catch (error) {
      if (!runtime.disposed) {
        page.setData({
          syncLabel: "上次进度待确认",
          inlineError: errorMessage(error, "上次保存结果未知，将继续确认刚才那次保存。"),
        });
      }
      return false;
    }
  })();
  runtime.saving = operation;
  try {
    await operation;
  } finally {
    if (runtime.saving === operation) runtime.saving = null;
  }
}

// ═══════ 交卷（先落 checkpoint 与轨迹，再提交；结果未知靠本地暂存恢复） ═══════

async function executeSubmit(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  pending?: PendingMockSubmit,
): Promise<void> {
  if (runtime.submitInFlight) return;
  runtime.submitInFlight = true;
  page.setData({
    interactionLocked: true,
    submitting: true,
    syncLabel: "正在交卷",
    sheetOpen: false,
  });
  try {
    if (pending) {
      // 恢复场景：等待在途 checkpoint 结束即可，命令以暂存为准
      if (runtime.saving) await runtime.saving;
    } else {
      const saved = await saveCheckpoint(page, runtime);
      if (!saved) {
        page.setData({ interactionLocked: false, submitting: false });
        return;
      }
    }
    if (runtimes.get(page) !== runtime) return;
    if (!(await flushTrajectory(runtime))) {
      throw new Error("作答过程还没确认成功，请保持网络后重试交卷。");
    }
    if (pending && runtime.state) {
      runtime.state = applyPendingSubmit(runtime.state, pending.command);
    }
    const session = requiredSession(runtime);
    const idempotencyKey = pending?.idempotencyKey ?? idemKey();
    const command = pending?.command ?? buildSubmitCommand(requiredState(runtime));
    const submission: PendingMockSubmit = pending ?? {
      kind: "submit",
      ownerUserId: runtime.ownerId,
      sessionId: session.sessionId,
      idempotencyKey,
      command,
      storedAt: new Date().toISOString(),
    };
    storePendingMockSubmit(submission);
    const receipt = await submitWithConflictRetry(page, runtime, submission);
    if (receipt === null) {
      // 云端已完成交卷：本机不再重复提交，直接进入已交卷态
      clearPendingMockSubmit(runtime.ownerId, session.sessionId);
      clearPendingMockCheckpoint(runtime.ownerId, session.sessionId);
      if (runtime.disposed) return;
      stopClock(runtime);
      terminateAsSubmitted(page, runtime, requiredSession(runtime), "云端已经完成交卷。");
      return;
    }
    clearPendingMockSubmit(runtime.ownerId, session.sessionId);
    clearPendingMockCheckpoint(runtime.ownerId, session.sessionId);
    if (runtime.disposed) return;
    stopClock(runtime);
    const review = receipt.review;
    page.setData({
      viewState: "submitted",
      interactionLocked: true,
      submitting: false,
      syncLabel: "已交卷",
      reportSessionId: receipt.sessionId,
      submitted: {
        hasReceipt: true,
        performanceIndex: review.performanceIndex,
        rangeLower: review.performanceRange.lower,
        rangeUpper: review.performanceRange.upper,
        estimatedScore: review.estimatedScore,
        metricDefinition: review.metricDefinition,
        submittedAtLabel: formatSubmittedAt(review.submittedAt),
      },
    });
  } catch (error) {
    if (runtime.disposed) return;
    page.setData({
      interactionLocked: false,
      submitting: false,
      syncLabel: "交卷结果待确认",
      inlineError: errorMessage(error, "交卷结果未知，已保留同一命令，重新进入会继续确认。"),
    });
  } finally {
    runtime.submitInFlight = false;
  }
}

/** 交卷 409：刷新会话；若仍可答则按最新 revision 重放本机答案再提交一次 */
async function submitWithConflictRetry(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  submission: PendingMockSubmit,
): Promise<MockExamSubmitResponse | null> {
  try {
    return await apiSubmitExam(submission.sessionId, submission.command, submission.idempotencyKey);
  } catch (error) {
    if (!isConflict(error)) throw error;
    const latest = await apiGetSession(submission.sessionId);
    if (runtimes.get(page) !== runtime) throw error;
    if (latest.status !== "assigned") {
      runtime.session = latest;
      return null;
    }
    runtime.session = latest;
    runtime.state = rebaseLocalOntoSession(requiredState(runtime), latest);
    const rebased: PendingMockSubmit = {
      ...submission,
      idempotencyKey: idemKey(),
      command: buildSubmitCommand(runtime.state),
      storedAt: new Date().toISOString(),
    };
    clearPendingMockSubmit(runtime.ownerId, submission.sessionId);
    storePendingMockSubmit(rebased);
    return apiSubmitExam(rebased.sessionId, rebased.command, rebased.idempotencyKey);
  }
}

async function resumePendingSubmit(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  pending: PendingMockSubmit,
): Promise<void> {
  await executeSubmit(page, runtime, pending);
}

// ═══════ 作答轨迹（本地排队，逐条上行；409 时按服务端序号重排） ═══════

function initializeTrajectory(runtime: PageRuntime, session: MockExamSession): void {
  const pending = loadPendingMockTrajectory(runtime.ownerId, session.sessionId);
  runtime.nextEventSeq = Math.max(
    session.nextTrajectoryEventSeq,
    ...pending.map((item) => item.command.eventSeq + 1),
  );
  if (pending.length > 0) void flushTrajectory(runtime);
}

function queueTrajectory(
  runtime: PageRuntime,
  eventType: MockExamTrajectoryEventCommand["eventType"],
  questionId: string | null,
  payload: TrajectoryPayload,
): void {
  const session = runtime.session;
  if (!session || session.status !== "assigned" || runtime.disposed) return;
  const eventSeq = runtime.nextEventSeq;
  runtime.nextEventSeq += 1;
  const now = new Date().toISOString();
  appendPendingMockTrajectory({
    ownerUserId: runtime.ownerId,
    sessionId: session.sessionId,
    idempotencyKey: `mock-trajectory:${session.sessionId}:${eventSeq}`,
    command: {
      eventSeq,
      eventType,
      questionId,
      clientOccurredAt: now,
      sessionRevision: session.revision,
      payload,
    },
    storedAt: now,
  });
  void flushTrajectory(runtime);
}

function flushTrajectory(runtime: PageRuntime): Promise<boolean> {
  if (runtime.trajectoryFlush) return runtime.trajectoryFlush;
  const operation = performTrajectoryFlush(runtime);
  runtime.trajectoryFlush = operation;
  void operation.finally(() => {
    if (runtime.trajectoryFlush === operation) runtime.trajectoryFlush = null;
  });
  return operation;
}

async function performTrajectoryFlush(runtime: PageRuntime): Promise<boolean> {
  const session = runtime.session;
  if (!session) return true;
  let rebased = false;
  while (!runtime.disposed) {
    const pending = loadPendingMockTrajectory(runtime.ownerId, session.sessionId);
    const item = pending.at(0);
    if (!item) return true;
    try {
      await apiRecordTrajectory(item.sessionId, item.command, item.idempotencyKey);
      removePendingMockTrajectory(runtime.ownerId, item.sessionId, item.idempotencyKey);
    } catch (error) {
      if (!isConflict(error) || rebased) return false;
      try {
        const latest = await apiGetSession(session.sessionId);
        if (latest.status !== "assigned") {
          replacePendingMockTrajectory(runtime.ownerId, session.sessionId, []);
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
  const pending = loadPendingMockTrajectory(runtime.ownerId, session.sessionId).map(
    (item, index): PendingMockTrajectoryEvent => {
      const eventSeq = firstEventSeq + index;
      return {
        ...item,
        idempotencyKey: `mock-trajectory:${session.sessionId}:${eventSeq}:rebase`,
        command: { ...item.command, eventSeq, sessionRevision: session.revision },
      };
    },
  );
  replacePendingMockTrajectory(runtime.ownerId, session.sessionId, pending);
  runtime.nextEventSeq = firstEventSeq + pending.length;
}

// ═══════ 计时（按统一结束时间倒计时，到点自动交卷） ═══════

function startClock(page: MockExamPageInstance, runtime: PageRuntime): void {
  stopClock(runtime);
  if (!runtime.visible || page.data.viewState !== "answering") return;
  runtime.timerId = setInterval(() => {
    refreshClock(page, runtime);
  }, 1000);
}

function stopClock(runtime: PageRuntime): void {
  if (runtime.timerId === null) return;
  clearInterval(runtime.timerId);
  runtime.timerId = null;
}

function refreshClock(page: MockExamPageInstance, runtime: PageRuntime): void {
  const session = runtime.session;
  if (!session || runtime.disposed) return;
  const remaining = remainingSeconds(session.deadlineAt, Date.now());
  page.setData({ remainingText: formatRemainingTime(remaining) });
  if (remaining === 0 && page.data.viewState === "answering" && !runtime.submitInFlight) {
    stopClock(runtime);
    void executeSubmit(page, runtime);
  }
}

// ═══════ 小工具 ═══════

function terminateAsSubmitted(
  page: MockExamPageInstance,
  runtime: PageRuntime,
  session: MockExamSession,
  inlineError: string,
): void {
  runtime.session = session;
  runtime.state = null;
  stopClock(runtime);
  page.setData({
    viewState: "submitted",
    title: session.title,
    reportSessionId: session.sessionId,
    interactionLocked: true,
    submitting: false,
    sheetOpen: false,
    toolsOpen: false,
    draftOpen: false,
    inlineError,
    submitted: emptySubmittedView(),
  });
}

function emptySubmittedView(): SubmittedView {
  return {
    hasReceipt: false,
    performanceIndex: 0,
    rangeLower: 0,
    rangeUpper: 0,
    estimatedScore: 0,
    metricDefinition: "",
    submittedAtLabel: "",
  };
}

function sessionWithReceiptRevision(
  session: MockExamSession,
  receipt: MockExamCheckpointReceipt,
): MockExamSession {
  if (receipt.sessionId !== session.sessionId) throw new Error("保存回执不属于本场模考");
  if (receipt.revision <= session.revision) return session;
  return {
    ...session,
    revision: receipt.revision,
    checkpoint: receipt.checkpoint,
  };
}

function isConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

function currentQuestionId(runtime: PageRuntime): string {
  const state = requiredState(runtime);
  const questionId = state.questionOrder[state.currentQuestionIndex];
  if (!questionId) throw new Error("当前题不在整卷内");
  return questionId;
}

function requiredRuntime(page: MockExamPageInstance): PageRuntime {
  const runtime = runtimes.get(page);
  if (!runtime || runtime.disposed) throw new Error("模考页运行时已销毁");
  return runtime;
}

function requiredState(runtime: PageRuntime): MockRuntimeState {
  if (!runtime.state) throw new Error("整卷尚未加载");
  return runtime.state;
}

function requiredSession(runtime: PageRuntime): MockExamSession {
  if (!runtime.session) throw new Error("模考会话尚未加载");
  return runtime.session;
}

function ownerKey(): string {
  const deviceId = wx.getStorageSync(config.storageKeys.deviceId) as string | "";
  return deviceId || "local-device";
}

function formatSubmittedAt(iso: string): string {
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return "";
  const date = new Date(time);
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")} 交卷`;
}

function dataset(event: WechatMiniprogram.BaseEvent, name: string): string {
  const value: unknown = event.currentTarget.dataset[name];
  if (typeof value !== "string" && typeof value !== "number") {
    throw new Error(`模考交互缺少 ${name}`);
  }
  return String(value);
}

function failPage(page: MockExamPageInstance, error: unknown, title: string): void {
  const apiError = error instanceof ApiError ? error : null;
  page.setData({
    viewState: "error",
    errorTitle: title,
    errorDesc: errorMessage(error, "没有找到可继续作答的整卷。"),
    requestId: apiError?.requestId ?? "",
  });
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message.trim() ? error.message : fallback;
}

// ═══════ API（v1 裸响应用 authedRequest，v2 envelope 用 authedV2） ═══════

function apiCreateWeekly(): Promise<MockExamSession> {
  return authedRequest<MockExamSession>({
    method: "POST",
    path: "/api/v1/mock-exams/weekly",
    body: {},
    idempotencyKey: idemKey(),
  });
}

function apiGetSession(sessionId: string): Promise<MockExamSession> {
  return authedRequest<MockExamSession>({
    method: "GET",
    path: `/api/v1/mock-exams/${encodeURIComponent(sessionId)}`,
  });
}

function apiSaveCheckpoint(
  sessionId: string,
  command: MockExamCheckpointRequest,
  idempotencyKey: string,
): Promise<MockExamCheckpointReceipt> {
  return authedRequest<MockExamCheckpointReceipt>({
    method: "PUT",
    path: `/api/v1/mock-exams/${encodeURIComponent(sessionId)}/checkpoint`,
    body: command,
    idempotencyKey,
  });
}

function apiSubmitExam(
  sessionId: string,
  command: MockExamSubmitRequest,
  idempotencyKey: string,
): Promise<MockExamSubmitResponse> {
  return authedRequest<MockExamSubmitResponse>({
    method: "POST",
    path: `/api/v1/mock-exams/${encodeURIComponent(sessionId)}/submit`,
    body: command,
    idempotencyKey,
  });
}

function apiRecordTrajectory(
  sessionId: string,
  command: MockExamTrajectoryEventCommand,
  idempotencyKey: string,
): Promise<unknown> {
  return authedV2<unknown>({
    method: "POST",
    path: `/api/v2/mock-exams/${encodeURIComponent(sessionId)}/trajectory-events`,
    body: command,
    idempotencyKey,
  });
}

function apiReportDispute(
  sessionId: string,
  command: ReportQuestionDisputeCommand,
  idempotencyKey: string,
): Promise<QuestionDisputeReceipt> {
  return authedRequest<QuestionDisputeReceipt>({
    method: "POST",
    path: `/api/v1/mock-exams/${encodeURIComponent(sessionId)}/disputes`,
    body: command,
    idempotencyKey,
  });
}
