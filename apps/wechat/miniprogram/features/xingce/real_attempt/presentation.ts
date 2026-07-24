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
  positionNumber: number;
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
  metrics: Array<{
    code: "correct" | "elapsed" | "overtime" | "guessed" | "abandoned";
    label: string;
    value: string;
    tone: "default" | "warning";
  }>;
  confidenceCalibration: Array<{
    code: "confident_correct" | "hesitant_correct" | "guessed_correct" | "incorrect";
    label: string;
    value: number;
    tone: "success" | "primary" | "warning" | "danger";
  }>;
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
      summary: "这次历史作答缺少完整记录，岸岸不会猜测真会、猜对、用时或主动放弃。",
      correctCount: result.correctCount,
      totalCount: result.totalCount,
      metrics: [
        {
          code: "correct",
          label: "正确",
          value: `${String(result.correctCount)}/${String(result.totalCount)}`,
          tone: "default",
        },
        {
          code: "elapsed",
          label: "总用时",
          value: formatElapsed(session.totalElapsedSeconds),
          tone: "default",
        },
      ],
      confidenceCalibration: [],
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
    metrics: resultMetrics(result, session.totalElapsedSeconds),
    confidenceCalibration: confidenceCalibration(result),
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
      description: "这条历史判断缺少可核对的原始记录，岸岸不会猜测补全。",
    },
    processing: {
      label: "岸岸正在整理错因",
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
      label: "错因依据不足",
      description: "岸岸还缺少足够信息，不会直接猜错因；你可以否定、自己补充或稍后处理。",
    },
    terminated: {
      label: "本次错因整理已终止",
      description: "已保留现有记录；你可以否定、自己补充或稍后处理。",
    },
  }[candidate.state];
  const candidateVersionId = candidate.candidateVersionId?.trim() || null;
  const hasBoundVersion = candidateVersionId !== null;
  return {
    state: candidate.state,
    stateLabel: copy.label,
    stateDescription: copy.description,
    candidateVersionId,
    candidateVersionLabel: candidateVersionId ? "本次待确认判断" : "当前没有可确认的判断",
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
    positionNumber: index + 1,
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

function resultMetrics(
  result: XingceAttemptResult,
  totalElapsedSeconds: number,
): XingceResultView["metrics"] {
  const group = result.groupSummary;
  if (!group) {
    return [
      {
        code: "correct",
        label: "正确",
        value: `${String(result.correctCount)}/${String(result.totalCount)}`,
        tone: "default",
      },
      {
        code: "elapsed",
        label: "总用时",
        value: formatElapsed(totalElapsedSeconds),
        tone: "default",
      },
    ];
  }
  return [
    {
      code: "correct",
      label: "正确",
      value: `${String(result.correctCount)}/${String(result.totalCount)}`,
      tone: "default",
    },
    {
      code: "elapsed",
      label: "总用时",
      value: formatElapsed(totalElapsedSeconds),
      tone: "default",
    },
    {
      code: "overtime",
      label: "超建议",
      value: String(group.advisoryExceeded),
      tone: group.advisoryExceeded > 0 ? "warning" : "default",
    },
    {
      code: "guessed",
      label: "猜对",
      value: String(group.guessedCorrect),
      tone: group.guessedCorrect > 0 ? "warning" : "default",
    },
    {
      code: "abandoned",
      label: "主动放弃",
      value: String(group.activeAbandon),
      tone: "default",
    },
  ];
}

function confidenceCalibration(
  result: XingceAttemptResult,
): XingceResultView["confidenceCalibration"] {
  const counts = {
    confident_correct: 0,
    hesitant_correct: 0,
    guessed_correct: 0,
    incorrect: 0,
  };
  for (const item of result.items) {
    if (item.objectiveVerdict !== "correct") {
      if (item.disposition !== "active_abandon") counts.incorrect += 1;
      continue;
    }
    if (item.confidenceEvidence === "high") counts.confident_correct += 1;
    else if (item.confidenceEvidence === "medium") counts.hesitant_correct += 1;
    else counts.guessed_correct += 1;
  }
  return [
    {
      code: "confident_correct",
      label: "高信心且正确",
      value: counts.confident_correct,
      tone: "success",
    },
    {
      code: "hesitant_correct",
      label: "中等信心且正确",
      value: counts.hesitant_correct,
      tone: "primary",
    },
    {
      code: "guessed_correct",
      label: "低信心但正确",
      value: counts.guessed_correct,
      tone: "warning",
    },
    {
      code: "incorrect",
      label: "已作答但错误",
      value: counts.incorrect,
      tone: "danger",
    },
  ];
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
  return { label: "答对·信心未记录", tone: "neutral" };
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
      label: "记录有效",
      description: "当前复测结论可以进入掌握判断。",
    },
    pending_dispute: {
      label: "争议处理中",
      description: "在争议确认前，这次复测不会推高或降低掌握判断。",
    },
    confirmed_invalidated: {
      label: "记录已确认失效",
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
    sourceVersionLabel: "基于上一次掌握记录",
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

function syncLabel(runtime: TaskRuntimeState): string {
  if (runtime.syncState === "saving") return "正在保存进度";
  if (runtime.syncState === "pending") return "有修改待保存";
  if (runtime.syncState === "conflict") return "内容已更新";
  if (runtime.syncState === "error") return "保存未确认";
  return "进度已保存";
}

function modeLabel(mode: XingceTaskSession["mode"]): string {
  if (mode === "baseline") return "行测摸底";
  if (mode === "verification") return "独立复测";
  return "行测专项训练";
}
