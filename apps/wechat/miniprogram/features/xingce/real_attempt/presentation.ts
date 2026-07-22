import type {
  XingceAttemptResult,
  XingceCauseCandidate,
  XingceGroupSummary,
  XingceLearnerMaterial,
  XingceLearnerQuestion,
  XingceTaskSession,
  XingceVerificationOutcome,
} from "../../../api/xingce-learning";
import type { QuestionAttemptDraft, TaskRuntimeState } from "../../../task-runtime/contracts";
import type { RichContentBlock } from "../../../task-runtime/content-renderer/model";
import { assertFactoredXingceResult, isFactoredXingceResult } from "./contracts";

export interface XingceOptionView {
  optionId: string;
  label: string;
  text: string;
  html: string | null;
  content: RichContentBlock[];
  selected: boolean;
  eliminated: boolean;
}

export interface XingceQuestionView {
  questionId: string;
  contentVersionId: string;
  moduleName: string;
  stem: string;
  stemHtml: string | null;
  stemContent: RichContentBlock[];
  positionLabel: string;
  options: XingceOptionView[];
  material: XingceLearnerMaterial | null;
  selectedOptionIds: string[];
  confidence: QuestionAttemptDraft["confidence"] | null;
  confidenceLabel: string;
  marked: boolean;
  skipped: boolean;
  elapsedLabel: string;
  draftText: string;
}

export interface XingceTaskView {
  title: string;
  modeLabel: string;
  current: number;
  total: number;
  elapsedLabel: string;
  syncLabel: string;
  question: XingceQuestionView;
  materialAvailable: boolean;
  sharedMaterial: boolean;
  answeredCount: number;
  skippedCount: number;
  unansweredCount: number;
}

export interface XingceResultItemView {
  questionId: string;
  positionLabel: string;
  resultLabel: string;
  resultTone: "success" | "warning" | "danger" | "neutral";
  objectiveLabel: string;
  confidenceLabel: string;
  dispositionLabel: string;
  timingLabel: string;
  timingExceeded: boolean;
  selectedLabel: string;
  correctLabel: string;
  explanation: string;
  explanationSource: string;
}

export interface XingceGroupSummaryView {
  outcome: XingceGroupSummary["outcome"];
  outcomeLabel: string;
  outcomeTone: "success" | "warning" | "neutral";
  description: string;
  counts: Array<{ code: string; label: string; value: number }>;
  nextActionLabel: string;
}

export interface XingceVerificationOutcomeView {
  outcome: XingceVerificationOutcome["outcome"];
  outcomeLabel: string;
  outcomeTone: "success" | "warning" | "danger";
  evidenceState: XingceVerificationOutcome["evidenceState"];
  evidenceLabel: string;
  evidenceDescription: string;
  stageLabel: string;
  sourceVersionLabel: string;
}

export interface XingceCauseCandidateView {
  state: XingceCauseCandidate["state"];
  stateLabel: string;
  stateDescription: string;
  candidateVersionId: string | null;
  candidateVersionLabel: string;
  causeCode: string;
  summary: string;
  improvement: string;
  question: string;
  rationale: string;
  explanation: string;
  missingEvidence: string[];
  evidenceRefs: string[];
  limitations: string[];
  canConfirm: boolean;
  canDecide: boolean;
}

export interface XingceResultView {
  availability: "factored" | "legacy_result_unavailable";
  headline: string;
  summary: string;
  correctCount: number;
  totalCount: number;
  items: XingceResultItemView[];
  group: XingceGroupSummaryView | null;
  verification: XingceVerificationOutcomeView | null;
  reviewId: string;
}

const confidenceLabels: Record<NonNullable<QuestionAttemptDraft["confidence"]>, string> = {
  low: "不确定",
  medium: "比较确定",
  high: "非常确定",
};

export function presentXingceTask(
  session: XingceTaskSession,
  runtime: TaskRuntimeState,
): XingceTaskView {
  const questionId = runtime.questionOrder[runtime.currentQuestionIndex];
  const question = session.bundle.questions.find((item) => item.questionId === questionId);
  const attempt = questionId ? runtime.attempts[questionId] : undefined;
  if (!question || !attempt) throw new Error("当前行测题目与作答进度不一致。");
  const answeredCount = runtime.questionOrder.filter(
    (id) => (runtime.attempts[id]?.selectedOptionIds.length ?? 0) > 0,
  ).length;
  const skippedCount = runtime.questionOrder.filter((id) => runtime.attempts[id]?.skipped).length;
  return {
    title: "今日行测真实训练",
    modeLabel: modeLabel(session.mode),
    current: runtime.currentQuestionIndex + 1,
    total: runtime.questionOrder.length,
    elapsedLabel: formatElapsed(runtime.totalElapsedSeconds),
    syncLabel: syncLabel(runtime),
    question: presentQuestion(
      question,
      attempt,
      session.bundle.materials,
      runtime.currentQuestionIndex,
      runtime.questionOrder.length,
    ),
    materialAvailable: question.materialId !== null,
    sharedMaterial: session.bundle.bundleType === "shared_material",
    answeredCount,
    skippedCount,
    unansweredCount: runtime.questionOrder.length - answeredCount - skippedCount,
  };
}

export function presentXingceResult(session: XingceTaskSession): XingceResultView | null {
  const result = session.result;
  const review = session.review;
  if (!result || !review) return null;
  if (!isFactoredXingceResult(result)) {
    return {
      availability: "legacy_result_unavailable",
      headline: "历史作答仅保留原始结果",
      summary: "这次作答使用旧评分合同，岸岸不会追溯猜测真会、猜对、用时或主动放弃事实。",
      correctCount: result.correctCount,
      totalCount: result.totalCount,
      items: [],
      group: null,
      verification: null,
      reviewId: review.reviewId,
    };
  }
  assertFactoredXingceResult(
    result,
    session.bundle.questions.map((question) => question.questionId),
    review.reflection?.itemInterpretations ?? [],
  );
  const group = result.groupSummary;
  if (!group) throw new Error("行测 3 题组结果缺失。");
  const interpreted = new Set(
    (review.reflection?.itemInterpretations ?? []).map((item) => item.questionId),
  );
  return {
    availability: "factored",
    headline: groupHeadline(group),
    summary: `客观答对 ${String(result.correctCount)} 题·真会 ${String(group.genuineCorrect)} 题·猜对 ${String(group.guessedCorrect)} 题`,
    correctCount: result.correctCount,
    totalCount: result.totalCount,
    items: result.items.map((item, index) =>
      presentResultItem(item, index, interpreted.has(item.questionId)),
    ),
    group: presentGroupSummary(group),
    verification: session.verificationOutcome
      ? presentVerificationOutcome(session.verificationOutcome)
      : null,
    reviewId: review.reviewId,
  };
}

export function presentXingceCauseCandidate(
  candidate: XingceCauseCandidate,
): XingceCauseCandidateView {
  const copy = {
    unavailable: {
      label: "历史候选不可用",
      description: "该候选没有可核验的证据版本，岸岸不会伪造或追溯补全。",
    },
    processing: {
      label: "岸岸正在整理错因证据",
      description: "已记下你的原话，错因归类好了会通知你。",
    },
    candidate_ready: {
      label: "岸岸给出了一个待确认错因",
      description: "这只是候选，确认、否定、替换或稍后处理都由你决定。",
    },
    followup_required: {
      label: "岸岸还需要一条信息",
      description: "回答下面的追问，岸岸再帮你分析一版。",
    },
    evidence_insufficient: {
      label: "错因证据不足",
      description: "岸岸不会在证据不足时猜错因；你可以明确否定、自己补充或稍后处理。",
    },
    terminated: {
      label: "本次错因整理已终止",
      description: "已保留现有证据与限制；你可以否定、自己补充或稍后处理。",
    },
  }[candidate.state];
  const candidateVersionId = candidate.candidateVersionId?.trim() || null;
  const hasBoundVersion = candidateVersionId !== null;
  return {
    state: candidate.state,
    stateLabel: copy.label,
    stateDescription: copy.description,
    candidateVersionId,
    candidateVersionLabel: candidateVersionId
      ? `候选版本 ${shortVersion(candidateVersionId)}`
      : "无可绑定候选版本",
    causeCode: candidate.causeCode ?? "",
    summary: candidate.summary ?? "",
    improvement: candidate.improvement ?? "",
    question: candidate.question ?? "",
    rationale: candidate.rationale ?? "",
    explanation: candidate.explanation ?? "",
    missingEvidence: [...(candidate.missingEvidence ?? [])],
    evidenceRefs: [...(candidate.evidenceRefs ?? [])],
    limitations: [...(candidate.limitations ?? [])],
    canConfirm: candidate.state === "candidate_ready" && hasBoundVersion,
    canDecide:
      hasBoundVersion &&
      ["candidate_ready", "evidence_insufficient", "terminated"].includes(candidate.state),
  };
}

export function formatElapsed(totalSeconds: number): string {
  const safeSeconds = Math.max(0, Math.floor(totalSeconds));
  const minutes = Math.floor(safeSeconds / 60);
  const seconds = safeSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function presentQuestion(
  question: XingceLearnerQuestion,
  attempt: QuestionAttemptDraft,
  materials: XingceLearnerMaterial[],
  index: number,
  total: number,
): XingceQuestionView {
  const selected = new Set(attempt.selectedOptionIds);
  const eliminated = new Set(attempt.eliminatedOptionIds);
  return {
    questionId: question.questionId,
    contentVersionId: question.contentVersionId,
    moduleName: question.moduleName,
    stem: question.stem,
    stemHtml: question.stemHtml,
    stemContent: normalizeRichContent(
      question.stemContent,
      question.stem,
      `${question.questionId}-stem`,
    ),
    positionLabel: `第 ${String(index + 1)} / ${String(total)} 题`,
    options: question.options.map((option) => ({
      ...option,
      content: normalizeRichContent(
        option.content,
        option.text,
        `${question.questionId}-option-${option.optionId}`,
      ),
      selected: selected.has(option.optionId),
      eliminated: eliminated.has(option.optionId),
    })),
    material:
      question.materialId === null
        ? null
        : normalizeMaterial(
            materials.find((material) => material.materialId === question.materialId) ?? null,
          ),
    selectedOptionIds: [...attempt.selectedOptionIds],
    confidence: attempt.confidence ?? null,
    confidenceLabel: attempt.confidence ? confidenceLabels[attempt.confidence] : "未标记",
    marked: attempt.marked,
    skipped: attempt.skipped,
    elapsedLabel: formatElapsed(attempt.elapsedSeconds),
    draftText: attempt.draftText,
  };
}

function normalizeMaterial(material: XingceLearnerMaterial | null): XingceLearnerMaterial | null {
  if (!material) return null;
  return {
    ...material,
    assets: Array.isArray(material.assets) ? material.assets : [],
    content: normalizeRichContent(
      material.content,
      material.text ?? material.title ?? "",
      `${material.materialId}-body`,
    ),
    anchors: Array.isArray(material.anchors) ? material.anchors : [],
  };
}

function normalizeRichContent(
  blocks: unknown,
  plainText: string | null | undefined,
  blockId: string,
): RichContentBlock[] {
  if (Array.isArray(blocks)) return blocks as RichContentBlock[];
  const text = plainText?.trim();
  return text ? [{ type: "paragraph", blockId, text }] : [];
}

function presentResultItem(
  item: XingceAttemptResult["items"][number],
  index: number,
  methodCorrectCalculationError: boolean,
): XingceResultItemView {
  const category = factoredCategory(item, methodCorrectCalculationError);
  const timing = item.timingEvidence;
  if (!timing || !item.objectiveVerdict || !item.disposition || !item.confidenceEvidence) {
    throw new Error("行测逐题事实不完整。");
  }
  return {
    questionId: item.questionId,
    positionLabel: `第 ${String(index + 1)} 题`,
    resultLabel: category.label,
    resultTone: category.tone,
    objectiveLabel:
      item.objectiveVerdict === "correct"
        ? "客观答对"
        : item.objectiveVerdict === "incorrect"
          ? "客观答错"
          : "客观未答",
    confidenceLabel: confidenceEvidenceLabel(item.confidenceEvidence),
    dispositionLabel: item.disposition === "active_abandon" ? "主动放弃" : "已作答",
    timingLabel:
      timing.status === "advisory_exceeded"
        ? `用时 ${formatElapsed(timing.elapsedSeconds)}·超过建议 ${formatElapsed(timing.advisorySeconds)}`
        : `用时 ${formatElapsed(timing.elapsedSeconds)}·建议 ${formatElapsed(timing.advisorySeconds)}`,
    timingExceeded: timing.status === "advisory_exceeded",
    selectedLabel: item.selectedOptionIds.length > 0 ? item.selectedOptionIds.join("、") : "未作答",
    correctLabel: item.correctOptionIds.join("、"),
    explanation: item.explanation ?? "这道题暂时没有解析。",
    explanationSource: item.explanationSource ?? "解析来源未提供",
  };
}

function factoredCategory(
  item: XingceAttemptResult["items"][number],
  methodCorrectCalculationError: boolean,
): { label: string; tone: XingceResultItemView["resultTone"] } {
  if (methodCorrectCalculationError) {
    return { label: "方法正确但计算错误", tone: "warning" };
  }
  if (item.disposition === "active_abandon") {
    return { label: "主动放弃", tone: "neutral" };
  }
  if (item.objectiveVerdict !== "correct") {
    return {
      label: item.objectiveVerdict === "unanswered" ? "不会·未答" : "不会·答错",
      tone: "danger",
    };
  }
  if (item.confidenceEvidence === "low") return { label: "猜对", tone: "warning" };
  if (item.confidenceEvidence === "medium" || item.confidenceEvidence === "high") {
    return { label: "真会", tone: "success" };
  }
  return { label: "答对·信心证据不足", tone: "neutral" };
}

function presentGroupSummary(summary: XingceGroupSummary): XingceGroupSummaryView {
  const outcomeCopy = {
    attained: {
      label: "3 题组已达成",
      tone: "success" as const,
      description: "3 题均有客观答对且中高信心证据。",
    },
    not_attained: {
      label: "3 题组未达成",
      tone: "warning" as const,
      description: "答对不等于真会；猜对、错题和未答会分开保留。",
    },
    interrupted: {
      label: "3 题组已中断",
      tone: "neutral" as const,
      description: "3 题均是主动放弃，不自动记为答错。",
    },
  }[summary.outcome];
  return {
    outcome: summary.outcome,
    outcomeLabel: outcomeCopy.label,
    outcomeTone: outcomeCopy.tone,
    description: outcomeCopy.description,
    counts: [
      { code: "genuine_correct", label: "真会", value: summary.genuineCorrect },
      { code: "guessed_correct", label: "猜对", value: summary.guessedCorrect },
      {
        code: "method_correct_calculation_error",
        label: "方法对·计算错",
        value: summary.methodCorrectCalculationError,
      },
      { code: "active_abandon", label: "主动放弃", value: summary.activeAbandon },
      { code: "advisory_exceeded", label: "超建议用时", value: summary.advisoryExceeded },
    ],
    nextActionLabel: groupNextAction(summary),
  };
}

function presentVerificationOutcome(
  outcome: XingceVerificationOutcome,
): XingceVerificationOutcomeView {
  const outcomeCopy = {
    passed: { label: "复测通过", tone: "success" as const },
    not_passed: { label: "复测未通过", tone: "warning" as const },
    disputed_or_invalidated: {
      label: "复测结果待核验",
      tone: "danger" as const,
    },
  }[outcome.outcome];
  const evidenceCopy = {
    valid: {
      label: "证据有效",
      description: "当前复测结论可以进入掌握判断。",
    },
    pending_dispute: {
      label: "争议处理中",
      description: "在争议确认前，这次复测不会推高或降低掌握判断。",
    },
    confirmed_invalidated: {
      label: "证据已确认失效",
      description: "这次复测仅保留历史记录，不再参与掌握计算。",
    },
  }[outcome.evidenceState];
  return {
    outcome: outcome.outcome,
    outcomeLabel: outcomeCopy.label,
    outcomeTone: outcomeCopy.tone,
    evidenceState: outcome.evidenceState,
    evidenceLabel: evidenceCopy.label,
    evidenceDescription: evidenceCopy.description,
    stageLabel: verificationStageLabel(outcome.verificationStage),
    sourceVersionLabel: `来源掌握版本 ${shortVersion(outcome.sourceMasteryVersionId)}`,
  };
}

function confidenceEvidenceLabel(
  confidence: NonNullable<XingceAttemptResult["items"][number]["confidenceEvidence"]>,
): string {
  return {
    low: "低信心",
    medium: "中等信心",
    high: "高信心",
    unreported: "信心未记录",
  }[confidence];
}

function groupHeadline(summary: XingceGroupSummary): string {
  if (summary.outcome === "attained") return "这组达标了，继续保持再巩固";
  if (summary.outcome === "interrupted") return "这组已中断，主动放弃不等于答错";
  return "这组未达成，逐题事实已分开保留";
}

function groupNextAction(summary: XingceGroupSummary): string {
  if (summary.pendingReflection) return "下一步：完成用户原话复盘";
  if (summary.pendingCause) return "下一步：完成错因确认";
  if (summary.immediateVerification) return "下一步：完成即时变式验证";
  if (summary.scheduled) return "下一步：按计划进入首次或延时复测";
  return "下一步：等岸岸排好新计划";
}

function verificationStageLabel(stage: XingceVerificationOutcome["verificationStage"]): string {
  return {
    immediate: "即时变式验证",
    first_review: "首次复测",
    delayed_review: "延时复测",
  }[stage];
}

function shortVersion(value: string): string {
  return value.length <= 12 ? value : `${value.slice(0, 8)}…${value.slice(-4)}`;
}

function syncLabel(runtime: TaskRuntimeState): string {
  if (runtime.syncState === "saving") return "正在保存进度";
  if (runtime.syncState === "pending") return "有修改待保存";
  if (runtime.syncState === "conflict") return "内容已更新";
  if (runtime.syncState === "error") return "保存未确认";
  return `已保存·v${String(runtime.revision)}`;
}

function modeLabel(mode: XingceTaskSession["mode"]): string {
  if (mode === "baseline") return "行测 Agent·受控基线";
  if (mode === "verification") return "行测 Agent·独立验证";
  return "行测 Agent·针对训练";
}
