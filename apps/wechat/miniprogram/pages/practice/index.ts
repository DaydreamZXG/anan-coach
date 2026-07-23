import { startTaskSession, saveTaskCheckpoint, submitTaskSession } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type {
  AnswerCheckpoint,
  SafeMaterial,
  SaveCheckpointCommand,
  SafeOption,
  SafeQuestion,
  TaskSessionProjection,
  XingceAttemptResult,
  XingceResultItem,
} from "../../api/generated/types.gen";

type ViewState = "loading" | "error" | "answering" | "result";

/** 本地作答记录（elapsedSeconds 为已结算的停留秒数） */
interface LocalAnswer {
  questionId: string;
  selectedOptionIds: string[];
  elapsedSeconds: number;
  marked: boolean;
}

/** 题卡顶部材料视图 */
interface MaterialView {
  title: string;
  text: string;
  collapsible: boolean;
  collapsed: boolean;
}

/** 答题卡栅格单元 */
interface SheetCell {
  index: number;
  seq: number;
  answered: boolean;
  marked: boolean;
  current: boolean;
}

/** 结果页选项视图 */
interface ResultOptionView {
  optionId: string;
  label: string;
  text: string;
  selected: boolean;
  correct: boolean;
}

/** 结果页逐题展开卡 */
interface ResultItemView {
  questionId: string;
  seq: number;
  moduleName: string;
  tagText: string;
  tagClass: string;
  stem: string;
  options: ResultOptionView[];
  explanation: string;
  expanded: boolean;
}

/** 材料超过约 5 行（按字符数估算）时提供展开/收起 */
const MATERIAL_COLLAPSE_CHARS = 150;
/** checkpoint 节流间隔 */
const SAVE_THROTTLE_MS = 800;

/** 秒 → mm:ss（分钟可超 60） */
function formatElapsed(totalSeconds: number): string {
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  const mm = m < 10 ? `0${m}` : `${m}`;
  const ss = s < 10 ? `0${s}` : `${s}`;
  return `${mm}:${ss}`;
}

Page({
  // ═══ 非响应式会话状态（不进 data，避免 setData 开销） ═══
  planTaskId: "",
  sessionId: "",
  reviewId: "",
  revision: 0,
  baseSeconds: 0,
  startedAt: 0,
  questionEnteredAt: 0,
  questions: [] as SafeQuestion[],
  materials: [] as SafeMaterial[],
  answers: [] as LocalAnswer[],
  saving: false,
  savePending: false,
  tickTimer: null as ReturnType<typeof setInterval> | null,
  saveTimer: null as ReturnType<typeof setTimeout> | null,

  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorDesc: "",
    requestId: "",
    errorRetryable: false,

    // ── 答题视图 ──
    currentIndex: 0,
    totalCount: 0,
    moduleName: "",
    elapsedText: "00:00",
    segments: [] as boolean[],
    stem: "",
    options: [] as SafeOption[],
    selectedOptionId: "",
    currentMarked: false,
    material: null as MaterialView | null,
    isFirst: true,
    isLast: false,
    submitting: false,
    conflict: false,
    sheetOpen: false,
    sheetCells: [] as SheetCell[],
    answeredCount: 0,

    // ── 结果视图 ──
    correctCount: 0,
    resultTotal: 0,
    accuracy: 0,
    resultItems: [] as ResultItemView[],
    reviewPending: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    const planTaskId = options.planTaskId;
    if (!planTaskId) {
      this.setData({
        viewState: "error",
        errorDesc: "缺少任务参数，请从今日页进入做题。",
        errorRetryable: false,
      });
      return;
    }
    this.planTaskId = planTaskId;
    this.openSession();
  },

  onHide() {
    // 还在答题态时，离开页面主动保存一次进度（不提交）
    if (this.data.viewState === "answering" && !this.data.conflict) {
      void this.flushCheckpoint();
    }
  },

  onUnload() {
    this.stopTick();
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
  },

  // ═══════ 加载会话 ═══════

  async openSession() {
    if (!this.planTaskId) return;
    this.setData({ viewState: "loading" });
    try {
      const session = await startTaskSession(this.planTaskId);
      if (session.state !== "answering" && session.result) {
        // 续做到已交卷会话：直接看结果
        this.applyResult(session);
        return;
      }
      this.enterAnswering(session);
    } catch (err) {
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorDesc: "题组暂时打不开，稍后再试一次。",
        requestId: apiErr.requestId ?? "",
        errorRetryable: true,
      });
    }
  },

  enterAnswering(session: TaskSessionProjection) {
    this.sessionId = session.sessionId;
    this.revision = session.revision;
    this.baseSeconds = session.totalElapsedSeconds;
    this.startedAt = Date.now();
    this.questions = session.bundle.questions;
    this.materials = session.bundle.materials;

    // 续做：按题序恢复已保存的作答
    const saved = new Map(session.answers.map((a) => [a.questionId, a]));
    this.answers = this.questions.map((q) => {
      const prev = saved.get(q.questionId);
      return {
        questionId: q.questionId,
        selectedOptionIds: prev?.selectedOptionIds ? [...prev.selectedOptionIds] : [],
        elapsedSeconds: prev?.elapsedSeconds ?? 0,
        marked: prev?.marked ?? false,
      };
    });

    let index = this.questions.findIndex((q) => q.questionId === session.currentQuestionId);
    if (index < 0) index = 0;
    this.questionEnteredAt = Date.now();

    this.setData({
      viewState: "answering",
      conflict: false,
      submitting: false,
      currentIndex: index,
      elapsedText: formatElapsed(this.baseSeconds),
    });
    this.refreshCurrent();
    this.startTick();
  },

  /** 把当前题投影到 data（题号、题干、选项、选中态、进度段） */
  refreshCurrent() {
    const { currentIndex } = this.data;
    const q = this.questions[currentIndex];
    if (!q) return;
    const ans = this.answers[currentIndex];
    this.setData({
      totalCount: this.questions.length,
      moduleName: q.moduleName,
      stem: q.stem,
      options: q.options,
      selectedOptionId: ans?.selectedOptionIds[0] ?? "",
      currentMarked: ans?.marked ?? false,
      material: this.buildMaterialView(q),
      isFirst: currentIndex === 0,
      isLast: currentIndex === this.questions.length - 1,
      segments: this.answers.map((a) => a.selectedOptionIds.length > 0),
      answeredCount: this.answers.filter((a) => a.selectedOptionIds.length > 0).length,
    });
  },

  buildMaterialView(q: SafeQuestion): MaterialView | null {
    if (!q.materialId) return null;
    const m = this.materials.find((it) => it.materialId === q.materialId);
    if (!m || !m.text) return null;
    const collapsible = m.text.length > MATERIAL_COLLAPSE_CHARS;
    return {
      title: m.title ?? "",
      text: m.text,
      collapsible,
      collapsed: collapsible,
    };
  },

  // ═══════ 计时 ═══════

  startTick() {
    this.stopTick();
    this.tickTimer = setInterval(() => {
      this.setData({ elapsedText: formatElapsed(this.currentTotalSeconds()) });
    }, 1000);
  },

  stopTick() {
    if (this.tickTimer) {
      clearInterval(this.tickTimer);
      this.tickTimer = null;
    }
  },

  /** 累计总用时 = 会话基线 + 本次进入后的本地秒数 */
  currentTotalSeconds(): number {
    return this.baseSeconds + Math.floor((Date.now() - this.startedAt) / 1000);
  },

  /** 切题前把当前题停留时长结算进本地答案 */
  settleCurrentQuestion() {
    const ans = this.answers[this.data.currentIndex];
    if (!ans) return;
    const now = Date.now();
    ans.elapsedSeconds += Math.floor((now - this.questionEnteredAt) / 1000);
    this.questionEnteredAt = now;
  },

  // ═══════ 作答交互 ═══════

  onSelectOption(e: WechatMiniprogram.TouchEvent) {
    if (this.data.conflict) return;
    const optionId = e.currentTarget.dataset.id as string;
    const ans = this.answers[this.data.currentIndex];
    if (!ans) return;
    ans.selectedOptionIds = [optionId];
    this.setData({
      selectedOptionId: optionId,
      segments: this.answers.map((a) => a.selectedOptionIds.length > 0),
      answeredCount: this.answers.filter((a) => a.selectedOptionIds.length > 0).length,
    });
    this.scheduleCheckpoint();
  },

  onToggleMark() {
    if (this.data.conflict) return;
    const ans = this.answers[this.data.currentIndex];
    if (!ans) return;
    ans.marked = !ans.marked;
    this.setData({ currentMarked: ans.marked });
    this.scheduleCheckpoint();
  },

  onToggleMaterial() {
    const material = this.data.material;
    if (!material) return;
    this.setData({ material: { ...material, collapsed: !material.collapsed } });
  },

  goTo(index: number) {
    if (index < 0 || index >= this.questions.length) return;
    this.settleCurrentQuestion();
    this.setData({ currentIndex: index });
    this.refreshCurrent();
    this.scheduleCheckpoint();
  },

  onPrev() {
    if (this.data.conflict || this.data.isFirst) return;
    this.goTo(this.data.currentIndex - 1);
  },

  onNext() {
    if (this.data.conflict || this.data.isLast) return;
    this.goTo(this.data.currentIndex + 1);
  },

  // ═══════ 答题卡 ═══════

  buildSheetCells(): SheetCell[] {
    return this.answers.map((a, i) => ({
      index: i,
      seq: i + 1,
      answered: a.selectedOptionIds.length > 0,
      marked: a.marked,
      current: i === this.data.currentIndex,
    }));
  },

  onOpenSheet() {
    if (this.data.conflict) return;
    this.setData({ sheetOpen: true, sheetCells: this.buildSheetCells() });
  },

  onCloseSheet() {
    this.setData({ sheetOpen: false });
  },

  onJumpTo(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index);
    this.setData({ sheetOpen: false });
    if (index !== this.data.currentIndex) {
      this.goTo(index);
    }
  },

  // ═══════ checkpoint 保存 ═══════

  scheduleCheckpoint() {
    if (this.saveTimer) clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.runCheckpoint();
    }, SAVE_THROTTLE_MS);
  },

  /** 取消节流并立即保存一次 */
  async flushCheckpoint(): Promise<void> {
    if (this.saveTimer) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    await this.runCheckpoint();
  },

  async runCheckpoint(): Promise<void> {
    if (this.data.conflict || this.data.viewState !== "answering") return;
    if (this.saving) {
      // 串行化：本次保存结束后再补一次
      this.savePending = true;
      return;
    }
    this.saving = true;
    try {
      const resp = await saveTaskCheckpoint(this.sessionId, this.buildCheckpointCommand());
      this.revision = resp.revision;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        this.onConflict();
      }
      // 其他失败静默：下次保存会重试，不打扰做题
    } finally {
      this.saving = false;
      if (this.savePending && !this.data.conflict) {
        this.savePending = false;
        this.scheduleCheckpoint();
      }
    }
  },

  buildCheckpointCommand(): SaveCheckpointCommand {
    this.settleCurrentQuestion();
    const current = this.questions[this.data.currentIndex];
    const answers: AnswerCheckpoint[] = this.answers.map((a) => ({
      questionId: a.questionId,
      selectedOptionIds: [...a.selectedOptionIds],
      elapsedSeconds: a.elapsedSeconds,
      marked: a.marked,
    }));
    return {
      answers,
      currentQuestionId: current?.questionId ?? "",
      expectedRevision: this.revision,
      totalElapsedSeconds: this.currentTotalSeconds(),
    };
  },

  /** 409：会话在别处被更新，锁定答题区（交卷保留可点） */
  onConflict() {
    if (this.data.conflict) return;
    this.setData({ conflict: true, sheetOpen: false });
    wx.showToast({ title: "会话已在别处更新，请重新进入", icon: "none" });
  },

  // ═══════ 交卷 ═══════

  onSubmitTap() {
    if (this.data.submitting) return;
    const unanswered = this.answers.filter((a) => a.selectedOptionIds.length === 0).length;
    if (unanswered > 0) {
      wx.showModal({
        title: "确认交卷？",
        content: `还有 ${unanswered} 题未答，未答按跳过处理。`,
        confirmText: "交卷",
        cancelText: "再想想",
        success: (res) => {
          if (res.confirm) void this.doSubmit();
        },
      });
      return;
    }
    void this.doSubmit();
  },

  async doSubmit() {
    this.setData({ submitting: true, sheetOpen: false });
    try {
      // 先把最新作答同步上去，再交卷
      await this.flushCheckpoint();
      const resp = await submitTaskSession(this.sessionId, {
        expectedRevision: this.revision,
        totalElapsedSeconds: this.currentTotalSeconds(),
      });
      this.stopTick();
      if (resp.result) {
        this.applyResult(resp);
      } else {
        wx.showToast({ title: "结果生成中，稍后可在今日页查看", icon: "none" });
        wx.switchTab({ url: "/pages/today/index" });
      }
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        this.onConflict();
      } else {
        wx.showToast({ title: "交卷失败，请再试一次", icon: "none" });
      }
    } finally {
      this.setData({ submitting: false });
    }
  },

  // ═══════ 结果视图 ═══════

  applyResult(session: TaskSessionProjection) {
    const result = session.result as XingceAttemptResult;
    const itemById = new Map(result.items.map((it) => [it.questionId, it]));
    const resultItems: ResultItemView[] = session.bundle.questions.map((q, i) =>
      this.buildResultItem(q, i, itemById.get(q.questionId) ?? null),
    );
    const accuracy =
      result.totalCount > 0 ? Math.round((result.correctCount / result.totalCount) * 100) : 0;
    // 续做进入时 sessionId 尚未写入，这里统一补上，供复盘跳转使用
    this.sessionId = session.sessionId;
    // 复盘闭环未走完（会话未到 completed）且已有复盘记录时，展示「去复盘」入口
    this.reviewId = session.state !== "completed" ? (session.review?.reviewId ?? "") : "";
    this.stopTick();
    this.setData({
      viewState: "result",
      correctCount: result.correctCount,
      resultTotal: result.totalCount,
      accuracy,
      resultItems,
      reviewPending: Boolean(this.reviewId),
    });
  },

  buildResultItem(q: SafeQuestion, index: number, item: XingceResultItem | null): ResultItemView {
    const resultCode = item?.resultCode ?? "skipped";
    const unanswered = item?.objectiveVerdict === "unanswered";
    let tagText = "跳过";
    let tagClass = "skipped";
    if (resultCode === "correct") {
      tagText = "答对";
      tagClass = "correct";
    } else if (resultCode === "incorrect") {
      tagText = unanswered ? "跳过" : "答错";
      tagClass = unanswered ? "skipped" : "incorrect";
    } else if (resultCode === "timeout") {
      tagText = "超时";
      tagClass = "timeout";
    } else if (resultCode === "guessed_correct") {
      tagText = "蒙对";
      tagClass = "guessed";
    }
    const selected = new Set(item?.selectedOptionIds ?? []);
    const correct = new Set(item?.correctOptionIds ?? []);
    return {
      questionId: q.questionId,
      seq: index + 1,
      moduleName: q.moduleName,
      tagText,
      tagClass,
      stem: q.stem,
      options: q.options.map((o) => ({
        optionId: o.optionId,
        label: o.label,
        text: o.text,
        selected: selected.has(o.optionId),
        correct: correct.has(o.optionId),
      })),
      explanation: item?.explanation ?? "解析整理中",
      expanded: false,
    };
  },

  onToggleResultItem(e: WechatMiniprogram.TouchEvent) {
    const index = Number(e.currentTarget.dataset.index);
    const key = `resultItems[${index}].expanded`;
    this.setData({ [key]: !this.data.resultItems[index]?.expanded });
  },

  // ═══════ 导航 ═══════

  /** 结果视图「去复盘」：带上作答会话与复盘记录标识 */
  onGoReflection() {
    if (!this.sessionId || !this.reviewId) return;
    wx.navigateTo({
      url: `/pages/reflection/index?sessionId=${encodeURIComponent(this.sessionId)}&reviewId=${encodeURIComponent(this.reviewId)}`,
    });
  },

  onBackToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },

  onPracticeMore() {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: "/pages/today/index" });
      },
    });
  },

  onBack() {
    wx.navigateBack();
  },
});
