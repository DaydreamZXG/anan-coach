import { authedV2, idemKey } from "../../api/endpoints";
import { ApiError } from "../../api/client";
import type {
  AiCauseCandidateState,
  CauseDecisionCommand,
  ItemInterpretation,
  ReflectionCommand,
  TaskSessionProjection,
  XingceResultItem,
} from "../../api/generated/types.gen";

type ViewState = "loading" | "ready" | "error";

/** 复盘阶段状态机（严格对应旧版 review.state） */
type ReflectionPhase =
  | "reflection"
  | "cause_processing"
  | "cause_followup"
  | "cause_decision"
  | "cause_insufficient"
  | "cause_terminated"
  | "completed";

/** 复盘思路标签（与旧版 contracts 一致） */
const REFLECTION_TAGS = [
  { code: "knowledge_gap", label: "知识盲区" },
  { code: "misread", label: "理解偏差" },
  { code: "method_error", label: "方法错误" },
  { code: "calculation_error", label: "计算失误" },
  { code: "time_pressure", label: "时间压力" },
  { code: "guessed", label: "猜测作答" },
  { code: "other", label: "其他" },
] as const;

interface TagOption {
  code: string;
  label: string;
  selected: boolean;
}

/** 逐题回顾视图（题干 + 我的答案 + 正确答案） */
interface ReviewItemView {
  questionId: string;
  seq: number;
  moduleName: string;
  stem: string;
  selectedLabel: string;
  correctLabel: string;
  verdictText: string;
  verdictClass: string;
}

/** 「方法正确但计算错误」可标记的错题选项 */
interface InterpretationOption {
  questionId: string;
  positionLabel: string;
  answerLabel: string;
  selected: boolean;
}

/** AI 错因候选视图 */
interface CauseCandidateView {
  stateLabel: string;
  stateDescription: string;
  summary: string;
  explanation: string;
  improvement: string;
  rationale: string;
  question: string;
  missingEvidence: string[];
  limitations: string[];
  canConfirm: boolean;
  canDecide: boolean;
}

/** 错因轮询间隔（cause_candidate_pending 时刷新） */
const CAUSE_POLL_MS = 1500;

Page({
  // ═══ 非响应式状态 ═══
  sessionId: "",
  reviewId: "",
  reviewRevision: 0,
  candidateVersionId: "",
  pollTimer: null as ReturnType<typeof setTimeout> | null,
  submitting: false,

  data: {
    statusBarHeight: 20,
    viewState: "loading" as ViewState,
    errorTitle: "",
    errorDesc: "",
    requestId: "",
    phase: "reflection" as ReflectionPhase,

    // ── 逐题回顾 ──
    correctCount: 0,
    totalCount: 0,
    reviewItems: [] as ReviewItemView[],

    // ── 复盘表单 ──
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    tagOptions: REFLECTION_TAGS.map((t) => ({ ...t, selected: false })) as TagOption[],
    interpretationOptions: [] as InterpretationOption[],

    // ── 错因候选 ──
    causeCandidate: null as CauseCandidateView | null,
    followupAnswer: "",
    replacementCause: "",

    // ── 提交态 ──
    pendingMessage: "",
    submitting: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    const sessionId = options.sessionId?.trim() ?? "";
    const reviewId = options.reviewId?.trim() ?? "";
    if (!sessionId || !reviewId) {
      this.setData({
        viewState: "error",
        errorTitle: "复盘地址不完整",
        errorDesc: "缺少作答会话或复盘记录参数，请从复盘中心进入。",
      });
      return;
    }
    this.sessionId = sessionId;
    this.reviewId = reviewId;
    void this.loadSession();
  },

  onUnload() {
    this.clearPoll();
  },

  // ═══════ 加载会话（复盘数据随会话投影下发） ═══════

  async loadSession(silent = false) {
    if (!silent) this.setData({ viewState: "loading" });
    try {
      const session = await authedV2<TaskSessionProjection>({
        method: "GET",
        path: `/api/v2/xingce/task-sessions/${encodeURIComponent(this.sessionId)}`,
      });
      this.applySession(session);
    } catch (err) {
      if (silent) {
        // 轮询失败不打断页面，下一轮继续
        this.schedulePollIfNeeded();
        return;
      }
      const apiErr = err as ApiError;
      this.setData({
        viewState: "error",
        errorTitle: "复盘状态没有载入",
        errorDesc: "请检查网络后重试。",
        requestId: apiErr.requestId ?? "",
      });
    }
  },

  /** 把会话投影映射到页面状态与阶段 */
  applySession(session: TaskSessionProjection) {
    const review = session.review;
    if (!review || review.reviewId !== this.reviewId) {
      this.setData({
        viewState: "error",
        errorTitle: "复盘记录与当前作答不匹配",
        errorDesc: "请返回复盘中心重新进入。",
      });
      return;
    }
    this.reviewRevision = review.revision;
    this.candidateVersionId = review.aiCandidate.candidateVersionId?.trim() ?? "";

    const phase = phaseOf(review.state);
    const reflection = review.reflection ?? null;

    this.setData({
      viewState: "ready",
      phase,
      correctCount: session.result?.correctCount ?? 0,
      totalCount: session.result?.totalCount ?? 0,
      reviewItems: buildReviewItems(session),
      causeCandidate: presentCauseCandidate(review.aiCandidate),
      // 已保存过的复盘内容回填；未保存时保留本地输入
      rawText: reflection?.rawText ?? this.data.rawText,
      strategyNote: reflection?.strategyNote ?? this.data.strategyNote,
      doubtNote: reflection?.doubtNote ?? this.data.doubtNote,
      tagOptions: REFLECTION_TAGS.map((t) => ({
        ...t,
        selected: reflection
          ? reflection.tags.includes(t.code)
          : (this.data.tagOptions.find((o) => o.code === t.code)?.selected ?? false),
      })),
      interpretationOptions: buildInterpretationOptions(
        session,
        new Set(
          (reflection?.itemInterpretations ?? this.selectedInterpretations()).map(
            (it) => it.questionId,
          ),
        ),
      ),
      pendingMessage: "",
    });
    this.schedulePollIfNeeded();
  },

  /** 错因生成中：定时静默刷新 */
  schedulePollIfNeeded() {
    this.clearPoll();
    if (this.data.phase !== "cause_processing") return;
    this.pollTimer = setTimeout(() => {
      this.pollTimer = null;
      void this.loadSession(true);
    }, CAUSE_POLL_MS);
  },

  clearPoll() {
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
  },

  onRetry() {
    void this.loadSession();
  },

  // ═══════ 阶段一：原话复盘 ═══════

  onRawTextInput(e: WechatMiniprogram.Input) {
    this.setData({ rawText: e.detail.value });
  },

  onStrategyInput(e: WechatMiniprogram.Input) {
    this.setData({ strategyNote: e.detail.value });
  },

  onDoubtInput(e: WechatMiniprogram.Input) {
    this.setData({ doubtNote: e.detail.value });
  },

  onToggleTag(e: WechatMiniprogram.TouchEvent) {
    if (this.submitting) return;
    const code = String(e.currentTarget.dataset.code ?? "");
    this.setData({
      tagOptions: this.data.tagOptions.map((t) =>
        t.code === code ? { ...t, selected: !t.selected } : t,
      ),
    });
  },

  onToggleInterpretation(e: WechatMiniprogram.TouchEvent) {
    if (this.submitting) return;
    const questionId = String(e.currentTarget.dataset.questionid ?? "");
    this.setData({
      interpretationOptions: this.data.interpretationOptions.map((it) =>
        it.questionId === questionId ? { ...it, selected: !it.selected } : it,
      ),
    });
  },

  selectedInterpretations(): ItemInterpretation[] {
    return this.data.interpretationOptions
      .filter((it) => it.selected)
      .map((it) => ({
        questionId: it.questionId,
        interpretation: "method_correct_calculation_error" as const,
      }));
  },

  async submitReflection() {
    if (this.submitting || !this.reviewRevision) return;
    const rawText = this.data.rawText.trim();
    const tags = this.data.tagOptions.filter((t) => t.selected).map((t) => t.code);
    if (!rawText || tags.length === 0) {
      wx.showToast({ title: "请写下真实想法并至少选择一个标签", icon: "none" });
      return;
    }
    const strategyNote = this.data.strategyNote.trim();
    const doubtNote = this.data.doubtNote.trim();
    const command: ReflectionCommand = {
      expectedRevision: this.reviewRevision,
      rawText,
      tags,
      itemInterpretations: this.selectedInterpretations(),
      ...(strategyNote ? { strategyNote } : {}),
      ...(doubtNote ? { doubtNote } : {}),
    };
    this.submitting = true;
    this.setData({ submitting: true, pendingMessage: "正在写入你的原话复盘…" });
    try {
      const session = await authedV2<TaskSessionProjection>({
        method: "PUT",
        path: `/api/v2/xingce/attempt-reviews/${encodeURIComponent(this.reviewId)}/reflection`,
        body: command,
        idempotencyKey: idemKey(),
      });
      this.applySession(session);
    } catch (err) {
      await this.handleWriteError(err, "复盘提交结果尚未确认，请重试。");
    } finally {
      this.submitting = false;
      this.setData({ submitting: false });
    }
  },

  // ═══════ 阶段二：错因追问 ═══════

  onFollowupInput(e: WechatMiniprogram.Input) {
    this.setData({ followupAnswer: e.detail.value });
  },

  async submitCauseFollowup() {
    if (this.submitting || !this.reviewRevision) return;
    const answer = this.data.followupAnswer.trim();
    if (!this.candidateVersionId) {
      wx.showToast({ title: "追问候选版本已变更，请刷新", icon: "none" });
      return;
    }
    if (!answer) {
      wx.showToast({ title: "请先回答追问", icon: "none" });
      return;
    }
    this.submitting = true;
    this.setData({ submitting: true, pendingMessage: "正在提交追问回答…" });
    try {
      const session = await authedV2<TaskSessionProjection>({
        method: "PUT",
        path: `/api/v2/xingce/attempt-reviews/${encodeURIComponent(this.reviewId)}/cause-followup`,
        body: {
          expectedRevision: this.reviewRevision,
          candidateVersionId: this.candidateVersionId,
          answer,
        },
        idempotencyKey: idemKey(),
      });
      this.applySession(session);
    } catch (err) {
      await this.handleWriteError(err, "追问回答尚未确认，请重试。");
    } finally {
      this.submitting = false;
      this.setData({ submitting: false });
    }
  },

  onRefreshCause() {
    if (this.submitting) return;
    this.setData({ pendingMessage: "正在刷新错因证据…" });
    void this.loadSession(true).finally(() => this.setData({ pendingMessage: "" }));
  },

  // ═══════ 阶段三：错因决定 ═══════

  onReplacementInput(e: WechatMiniprogram.Input) {
    this.setData({ replacementCause: e.detail.value });
  },

  async decideCause(decision: CauseDecisionCommand["decision"], replacementCause?: string) {
    if (this.submitting || !this.reviewRevision) return;
    const candidate = this.data.causeCandidate;
    if (!candidate?.canDecide || !this.candidateVersionId) {
      wx.showToast({ title: "错因候选版本不可决定，请刷新", icon: "none" });
      return;
    }
    if (decision === "confirm" && !candidate.canConfirm) {
      wx.showToast({ title: "当前证据不支持确认该错因", icon: "none" });
      return;
    }
    const command: CauseDecisionCommand = {
      expectedRevision: this.reviewRevision,
      candidateVersionId: this.candidateVersionId,
      decision,
      ...(replacementCause ? { replacementCause } : {}),
    };
    this.submitting = true;
    this.setData({ submitting: true, pendingMessage: "正在结算复盘与今日任务…" });
    try {
      const session = await authedV2<TaskSessionProjection>({
        method: "PUT",
        path: `/api/v2/xingce/attempt-reviews/${encodeURIComponent(this.reviewId)}/cause-decision`,
        body: command,
        idempotencyKey: idemKey(),
      });
      this.applySession(session);
    } catch (err) {
      await this.handleWriteError(err, "错因决定尚未确认，请重试。");
    } finally {
      this.submitting = false;
      this.setData({ submitting: false });
    }
  },

  onConfirmCause() {
    void this.decideCause("confirm");
  },

  onRejectCause() {
    void this.decideCause("reject");
  },

  onReplaceCause() {
    const replacementCause = this.data.replacementCause.trim();
    if (!replacementCause) {
      wx.showToast({ title: "请写下你认可的错因", icon: "none" });
      return;
    }
    void this.decideCause("replace", replacementCause);
  },

  onDeferCause() {
    void this.decideCause("defer");
  },

  // ═══════ 写失败处理：409 重新载入最新状态 ═══════

  async handleWriteError(err: unknown, fallback: string) {
    if (err instanceof ApiError && err.status === 409) {
      wx.showToast({ title: "复盘内容已更新，已载入最新状态", icon: "none" });
      await this.loadSession(true);
      return;
    }
    this.setData({ pendingMessage: fallback });
    wx.showToast({ title: fallback, icon: "none" });
  },

  // ═══════ 导航 ═══════

  onExit() {
    if (this.data.phase === "completed") {
      this.navigateAway();
      return;
    }
    wx.showModal({
      title: "本次复盘还没完成",
      content: "退出后今日任务会保持待复盘状态，可以稍后继续。",
      confirmText: "仍要退出",
      success: (res) => {
        if (res.confirm) this.navigateAway();
      },
    });
  },

  navigateAway() {
    wx.navigateBack({
      fail: () => wx.switchTab({ url: "/pages/today/index" }),
    });
  },

  onReturnToday() {
    wx.switchTab({ url: "/pages/today/index" });
  },
});

/** review.state → 页面阶段 */
function phaseOf(state: string): ReflectionPhase {
  switch (state) {
    case "reflection_required":
      return "reflection";
    case "cause_candidate_pending":
      return "cause_processing";
    case "cause_followup_required":
      return "cause_followup";
    case "cause_decision_required":
      return "cause_decision";
    case "cause_evidence_insufficient":
      return "cause_insufficient";
    case "cause_agent_terminated":
      return "cause_terminated";
    default:
      return "completed";
  }
}

/** 逐题回顾：题干 + 我的答案与正确答案（选项标识 → 选项标签） */
function buildReviewItems(session: TaskSessionProjection): ReviewItemView[] {
  const result = session.result;
  if (!result) return [];
  const itemById = new Map<string, XingceResultItem>(result.items.map((it) => [it.questionId, it]));
  return session.bundle.questions.map((q, index) => {
    const item = itemById.get(q.questionId) ?? null;
    const labelOf = (ids: string[]) =>
      ids.map((id) => q.options.find((o) => o.optionId === id)?.label ?? id).join("、");
    let verdictText = "跳过";
    let verdictClass = "skipped";
    if (item?.resultCode === "correct") {
      verdictText = "答对";
      verdictClass = "correct";
    } else if (item?.resultCode === "incorrect") {
      const unanswered = item.objectiveVerdict === "unanswered";
      verdictText = unanswered ? "跳过" : "答错";
      verdictClass = unanswered ? "skipped" : "incorrect";
    } else if (item?.resultCode === "timeout") {
      verdictText = "超时";
      verdictClass = "timeout";
    } else if (item?.resultCode === "guessed_correct") {
      verdictText = "蒙对";
      verdictClass = "guessed";
    }
    return {
      questionId: q.questionId,
      seq: index + 1,
      moduleName: q.moduleName,
      stem: q.stem,
      selectedLabel: item?.selectedOptionIds.length ? labelOf(item.selectedOptionIds) : "未作答",
      correctLabel: labelOf(item?.correctOptionIds ?? []),
      verdictText,
      verdictClass,
    };
  });
}

/** 「方法正确但计算错误」候选题：已作答的客观错题 */
function buildInterpretationOptions(
  session: TaskSessionProjection,
  selected: ReadonlySet<string>,
): InterpretationOption[] {
  const result = session.result;
  if (!result) return [];
  const itemById = new Map<string, XingceResultItem>(result.items.map((it) => [it.questionId, it]));
  return session.bundle.questions.flatMap((q, index) => {
    const item = itemById.get(q.questionId);
    if (!item || item.objectiveVerdict !== "incorrect" || item.disposition !== "answered") {
      return [];
    }
    const labelOf = (ids: string[]) =>
      ids.map((id) => q.options.find((o) => o.optionId === id)?.label ?? id).join("、");
    return [
      {
        questionId: q.questionId,
        positionLabel: `第 ${index + 1} 题`,
        answerLabel: `你的答案 ${labelOf(item.selectedOptionIds) || "未答"} · 正确答案 ${labelOf(item.correctOptionIds)}`,
        selected: selected.has(q.questionId),
      },
    ];
  });
}

/** AI 错因候选 → 视图（文案沿用旧版 presentation） */
function presentCauseCandidate(candidate: AiCauseCandidateState): CauseCandidateView {
  const copy = {
    unavailable: {
      label: "历史候选不可用",
      description: "该候选没有可核验的证据版本，不会伪造或追溯补全。",
    },
    processing: {
      label: "正在整理错因证据",
      description: "已记下你的原话，错因归类好了会出现在这里。",
    },
    candidate_ready: {
      label: "给出一个待确认错因",
      description: "这只是候选，确认、否定、替换或稍后处理都由你决定。",
    },
    followup_required: {
      label: "还需要一条信息",
      description: "回答下面的追问，再帮你分析一版。",
    },
    evidence_insufficient: {
      label: "错因证据不足",
      description: "证据不足时不会猜错因；你可以明确否定、自己补充或稍后处理。",
    },
    terminated: {
      label: "本次错因整理已终止",
      description: "已保留现有证据与限制；你可以否定、自己补充或稍后处理。",
    },
  }[candidate.state];
  const hasVersion = Boolean(candidate.candidateVersionId?.trim());
  return {
    stateLabel: copy.label,
    stateDescription: copy.description,
    summary: candidate.summary ?? "",
    explanation: candidate.explanation ?? "",
    improvement: candidate.improvement ?? "",
    rationale: candidate.rationale ?? "",
    question: candidate.question ?? "",
    missingEvidence: [...(candidate.missingEvidence ?? [])],
    limitations: [...(candidate.limitations ?? [])],
    canConfirm: candidate.state === "candidate_ready" && hasVersion,
    canDecide:
      hasVersion &&
      (candidate.state === "candidate_ready" ||
        candidate.state === "evidence_insufficient" ||
        candidate.state === "terminated"),
  };
}
