import type {
  XingceAnswerCheckpoint,
  XingceAttemptResult,
  XingceCauseCandidate,
  XingceGroupSummary,
  XingceLearnerMaterial,
  XingceLearnerQuestion,
  XingceResultItem as XingceApiResultItem,
  XingceTaskSession,
  XingceVerificationOutcome,
} from "../../../api/xingce-learning";
import {
  registeredTaskNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../../../../../app/navigation/routes";
import type {
  AnnotationStroke,
  AttemptActionEvent,
  QuestionAttemptDraft,
  TaskRuntimeState,
} from "../../../../../../task-runtime/contracts";
import type { RichContentBlock } from "../../../task-runtime/content-renderer/model";
import { stripOptionContentPrefix } from "../../../task-runtime/option-presentation";
import {
  assertFactoredXingceResult,
  isFactoredXingceResult,
} from "../../../../../../features/xingce/real_attempt/contracts";

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
  elapsedSeconds: number;
  /** 契约增量 v1：本题活跃毫秒时钟，供批注层盖真毫秒 atMs。 */
  elapsedMs: number;
  draftText: string;
  /** Figma 70:2：本题的批注笔迹。 */
  annotations: AnnotationStroke[];
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
  materialScopeLabel: string;
  sharedMaterial: boolean;
  answeredCount: number;
  skippedCount: number;
  unansweredCount: number;
}

export interface XingceResultItemView {
  questionId: string;
  positionNumber: number;
  positionLabel: string;
  stemContent: RichContentBlock[];
  material: XingceLearnerMaterial | null;
  materialPosition: XingceAnswerCheckpoint["materialPosition"] | null;
  factChips: Array<{
    code: "elapsed" | "confidence" | "eliminated" | "changed" | "marked";
    label: string;
    tone: "default" | "warning";
  }>;
  resultLabel: string;
  resultTone: "success" | "warning" | "danger" | "neutral";
  objectiveVerdict: NonNullable<XingceApiResultItem["objectiveVerdict"]>;
  confidenceEvidence: NonNullable<XingceApiResultItem["confidenceEvidence"]>;
  disposition: NonNullable<XingceApiResultItem["disposition"]>;
  objectiveLabel: string;
  confidenceLabel: string;
  dispositionLabel: string;
  timingLabel: string;
  timingExceeded: boolean;
  selectedLabel: string;
  correctLabel: string;
  selectedAnswerText: string;
  correctAnswerText: string;
  explanation: string;
  explanationSource: string;
  /** 用户键入的草稿；错因 Agent 可读取，结果页只用于过程证据回执。 */
  draftText: string;
  /** Figma 75:2/78:2：本题的批注笔迹与动作事实（缺失时为空数组）。 */
  annotations: AnnotationStroke[];
  actionEvents: AttemptActionEvent[];
  elapsedSeconds: number;
  trajectoryAvailable: boolean;
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

export interface XingceResultQuestionGroupView {
  code: "review" | "fragile" | "pending" | "mastered";
  label: string;
  description: string;
  tone: XingceResultItemView["resultTone"];
  count: number;
  items: Array<Pick<XingceResultItemView, "questionId" | "positionNumber">>;
}

export interface XingceProcessEvidenceView {
  hasEvidence: boolean;
  draftQuestionCount: number;
  anchoredSegmentCount: number;
  freeStrokeCount: number;
  scopeDescription: string;
}

/** 逐题候选错因 chip：纯展示，不承载任何决策交互。 */
export interface XingceQuestionCauseChipView {
  questionId: string;
  positionLabel: string;
  status: "candidate" | "evidence_insufficient";
  /** 候选态显示已发布错因分类的中文名；证据不足态固定「证据不足」。 */
  causeLabel: string;
  confidenceLabel: string;
  rationale: string;
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
  /** 契约增量：逐题候选（含显式证据不足态）；老候选没有该字段时为空。 */
  questionCandidates: XingceQuestionCauseChipView[];
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
    code: "correct" | "elapsed" | "overtime" | "changed" | "marked";
    label: string;
    value: string;
    tone: "default" | "warning";
  }>;
  confidenceCalibration: Array<{
    code:
      | "confident_correct"
      | "hesitant_correct"
      | "guessed_correct"
      | "confident_incorrect"
      | "hesitant_incorrect"
      | "guessed_incorrect";
    label: string;
    value: number;
    tone: "success" | "primary" | "warning" | "danger";
  }>;
  items: XingceResultItemView[];
  questionGroups: XingceResultQuestionGroupView[];
  processEvidence: XingceProcessEvidenceView;
  group: XingceGroupSummaryView | null;
  verification: XingceVerificationOutcomeView | null;
  reviewId: string;
}

export interface XingceAcceptedCorrectionView {
  source: "confirmed_candidate" | "user_replacement";
  correction: string;
  action: string;
}

export interface XingceReflectionClosureView {
  acceptedCorrection: XingceAcceptedCorrectionView | null;
  statusTitle: string;
  statusDescription: string;
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
    materialScopeLabel: materialScopeLabel(session, runtime, question.materialId),
    sharedMaterial: session.bundle.bundleType === "shared_material",
    answeredCount,
    skippedCount,
    unansweredCount: runtime.questionOrder.length - answeredCount - skippedCount,
  };
}

/**
 * Figma 2:77 的「(1-5题)」是共享同一材料的真实题号范围，
 * 由 bundle 中各题的 materialId 与作答顺序推出，不做任何假设性补齐。
 */
function materialScopeLabel(
  session: XingceTaskSession,
  runtime: TaskRuntimeState,
  materialId: string | null,
): string {
  if (!materialId) return "";
  const positions = session.bundle.questions
    .filter((item) => item.materialId === materialId)
    .map((item) => runtime.questionOrder.indexOf(item.questionId))
    .filter((index) => index >= 0)
    .map((index) => index + 1)
    .sort((left, right) => left - right);
  if (positions.length === 0) return "";
  const first = positions[0] as number;
  const last = positions[positions.length - 1] as number;
  return first === last ? `第 ${String(first)} 题` : `${String(first)}-${String(last)} 题`;
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
      questionGroups: [],
      processEvidence: processEvidence([]),
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
  if (!group) throw new Error("行测结果摘要缺失。");
  const interpreted = new Set(
    (review.reflection?.itemInterpretations ?? []).map((item) => item.questionId),
  );
  const questions = new Map(
    session.bundle.questions.map((question) => [question.questionId, question] as const),
  );
  const answers = new Map(session.answers.map((answer) => [answer.questionId, answer] as const));
  const resultByQuestion = new Map(result.items.map((item) => [item.questionId, item] as const));
  const orderedResultItems = session.bundle.questions.map((question) => {
    const item = resultByQuestion.get(question.questionId);
    if (!item) throw new Error("行测结果与题组顺序不一致。");
    return item;
  });
  const presentedItems = orderedResultItems.map((item, index) => {
    const question = questions.get(item.questionId);
    if (!question) throw new Error("行测结果与题目内容不一致。");
    return presentResultItem(
      item,
      index,
      interpreted.has(item.questionId),
      question,
      answers.get(item.questionId),
      session.bundle.materials,
    );
  });
  return {
    availability: "factored",
    headline: groupHeadline(group),
    summary: `客观答对 ${String(result.correctCount)} 题·真会 ${String(group.genuineCorrect)} 题·猜对 ${String(group.guessedCorrect)} 题`,
    correctCount: result.correctCount,
    totalCount: result.totalCount,
    metrics: resultMetrics(result, session.totalElapsedSeconds, session.answers),
    confidenceCalibration: confidenceCalibration(result),
    items: presentedItems,
    questionGroups: resultQuestionGroups(presentedItems),
    processEvidence: processEvidence(presentedItems),
    group: presentGroupSummary(group, result.totalCount),
    verification: session.verificationOutcome
      ? presentVerificationOutcome(session.verificationOutcome)
      : null,
    reviewId: review.reviewId,
  };
}

/**
 * 错因复盘只收真实错误/未答与低信心猜对题。
 * “答对但没有记录信心”和“仅超过建议用时”都是事实缺口，不等于错因。
 */
export function xingceReflectionReviewItems(
  items: readonly XingceResultItemView[],
): XingceResultItemView[] {
  return items.filter(
    (item) => item.objectiveVerdict !== "correct" || item.confidenceEvidence === "low",
  );
}

/** 镜像 backend xingce-cause-taxonomy@v1：只翻译已发布编码，未知编码原样展示。 */
const chipConfidenceLabels: Record<"low" | "medium" | "high", string> = {
  low: "把握较低",
  medium: "中等把握",
  high: "把握较高",
};

const xingceCauseDisplayNames: Record<string, string> = {
  stem_misread: "题干条件可能看反了",
  knowledge_gap: "这个知识点还没站稳",
  method_selection: "方法选错或步骤没理顺",
  calculation_error: "方法会用，但算错或抄错了",
  time_pressure: "时间太紧，作答变仓促",
  guessing: "依据不够，更多是在猜",
  active_abandon: "主动放弃或跳过了",
  other: "暂时归不到已有类型",
};

export function presentXingceCauseCandidate(
  candidate: XingceCauseCandidate,
  resultItems: ReadonlyArray<Pick<XingceResultItemView, "questionId" | "positionNumber">> = [],
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
      label: "岸岸已形成本次判断",
      description: "可以直接按这个判断继续；如果不符合你当时的真实想法，再否定或改写。",
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
  const positionByQuestion = new Map(
    resultItems.map((item) => [item.questionId, item.positionNumber] as const),
  );
  // 纯展示投影：只透传后端逐题事实，不在端上推断或补全错因。
  const questionCandidates: XingceQuestionCauseChipView[] = [
    ...(candidate.questionCandidates ?? []),
  ]
    .sort((left, right) => {
      const leftPosition = positionByQuestion.get(left.questionId) ?? Number.MAX_SAFE_INTEGER;
      const rightPosition = positionByQuestion.get(right.questionId) ?? Number.MAX_SAFE_INTEGER;
      return leftPosition - rightPosition;
    })
    .map((item) => {
      const position = positionByQuestion.get(item.questionId);
      return {
        questionId: item.questionId,
        positionLabel: position === undefined ? "本组题目" : `第 ${String(position)} 题`,
        status: item.status,
        causeLabel:
          item.status === "evidence_insufficient"
            ? "证据不足"
            : (xingceCauseDisplayNames[item.causeCode ?? ""] ?? item.causeCode ?? "证据不足"),
        confidenceLabel:
          item.status === "candidate" && item.confidence
            ? chipConfidenceLabels[item.confidence]
            : "",
        rationale: coachVoice(item.rationale ?? ""),
      };
    });
  return {
    state: candidate.state,
    stateLabel: copy.label,
    stateDescription: copy.description,
    candidateVersionId,
    candidateVersionLabel: candidateVersionId ? "本次岸岸判断" : "当前没有可用判断",
    causeCode: candidate.causeCode ?? "",
    summary: coachVoice(candidate.summary ?? ""),
    improvement: coachVoice(candidate.improvement ?? ""),
    question: candidate.question ?? "",
    rationale: coachVoice(candidate.rationale ?? ""),
    explanation: coachVoice(candidate.explanation ?? ""),
    missingEvidence: (candidate.missingEvidence ?? []).map(coachVoice),
    evidenceRefs: [...(candidate.evidenceRefs ?? [])],
    limitations: (candidate.limitations ?? []).map(coachVoice),
    questionCandidates,
    canConfirm: candidate.state === "candidate_ready" && hasBoundVersion,
    canDecide:
      hasBoundVersion &&
      ["candidate_ready", "evidence_insufficient", "terminated"].includes(candidate.state),
  };
}

/**
 * Projects only a server-settled, user-accepted correction into the completion UI.
 * Rejected, deferred, version-mismatched, or incomplete candidates are never promoted
 * to an accepted statement on the client.
 */
export function presentXingceReflectionClosure(
  session: XingceTaskSession,
): XingceReflectionClosureView {
  const acceptedCorrection = acceptedCorrectionFor(session);
  if (acceptedCorrection) {
    return {
      acceptedCorrection,
      statusTitle: "这次只改一个动作",
      statusDescription:
        acceptedCorrection.source === "confirmed_candidate"
          ? "先按已确认的动作做一遍，再用独立新题验证，不把一次答对当成已经掌握。"
          : "按你确认的说法和动作做一遍，再用独立新题验证。",
    };
  }
  return {
    acceptedCorrection: null,
    statusTitle: "这次没有形成确认纠偏",
    statusDescription:
      "当前没有同时得到用户确认且可核对的主要纠偏与具体动作，不会把候选判断包装成结论。",
  };
}

function acceptedCorrectionFor(session: XingceTaskSession): XingceAcceptedCorrectionView | null {
  const review = session.review;
  const decision = review?.causeDecision;
  if (
    !review ||
    !decision ||
    session.state !== "completed" ||
    !session.completion ||
    !decision.confirmedCauseVersionId?.trim()
  ) {
    return null;
  }
  if (decision.decision === "confirm") {
    const candidate = review.aiCandidate;
    const candidateVersionId = decision.candidateVersionId?.trim() ?? "";
    const correction = candidate.summary?.trim() ?? "";
    const action = candidate.improvement?.trim() ?? "";
    return candidate.state === "candidate_ready" &&
      candidateVersionId &&
      candidateVersionId === candidate.candidateVersionId?.trim() &&
      correction &&
      action
      ? { source: "confirmed_candidate", correction, action }
      : null;
  }
  if (decision.decision === "replace") {
    const correction = decision.replacementCause?.trim() ?? "";
    const action = review.reflection?.strategyNote?.trim() ?? "";
    return correction && action ? { source: "user_replacement", correction, action } : null;
  }
  return null;
}

/**
 * Selects an already-owned, ready verification task from the settled Today plan.
 * The returned URL must pass the shared task-route allowlist and bind back to the
 * exact plan task ID; the client never invents a variant or navigation target.
 */
export function presentXingceImmediateVerification(
  session: XingceTaskSession,
): RegisteredNavigationTarget | null {
  const acceptedCorrection = acceptedCorrectionFor(session);
  const decisionVersionId = session.review?.causeDecision?.decisionVersionId.trim() ?? "";
  if (!acceptedCorrection || !decisionVersionId) return null;
  const tasks = session.completion?.plan.tasks ?? [];
  for (const task of tasks) {
    if (
      task.track !== "xingce" ||
      task.stage !== "objective_verification" ||
      task.kind !== "verification" ||
      task.status !== "ready"
    ) {
      continue;
    }
    const decisionBindings = Array.isArray(task.evidenceRefs)
      ? task.evidenceRefs.filter(
          (reference) =>
            reference.owner === "objective_learning" &&
            reference.entityType === "xingce_cause_decision_version" &&
            reference.role === "immediate_verification_source",
        )
      : [];
    const decisionBinding = decisionBindings.at(0);
    if (
      decisionBindings.length !== 1 ||
      !decisionBinding ||
      decisionBinding.entityId !== decisionVersionId ||
      decisionBinding.versionId !== decisionVersionId
    ) {
      continue;
    }
    const navigation = registeredTaskNavigationTarget(task.startUrl, task.id);
    const [path] = navigation?.url.split("?", 1) ?? [];
    const expectedUrl = `${routes.xingceTaskIntro}?mode=verification&planTaskId=${encodeURIComponent(task.id)}`;
    if (
      !navigation ||
      navigation.mode !== "navigateTo" ||
      path !== routes.xingceTaskIntro ||
      navigation.url !== expectedUrl
    ) {
      continue;
    }
    return navigation;
  }
  return null;
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
      // 剥掉题库自带的 "A、" 前缀，避免与前端 label 视觉重复。
      content: stripOptionContentPrefix(
        normalizeRichContent(
          option.content,
          option.text,
          `${question.questionId}-option-${option.optionId}`,
        ),
        option.label,
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
    elapsedSeconds: attempt.elapsedSeconds,
    elapsedMs: attempt.elapsedMs ?? attempt.elapsedSeconds * 1000,
    draftText: attempt.draftText,
    annotations: attempt.annotations ?? [],
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
  if (Array.isArray(blocks) && blocks.length > 0) return blocks as RichContentBlock[];
  const text = plainText?.trim();
  return text ? [{ type: "paragraph", blockId, text }] : [];
}

function presentResultItem(
  item: XingceAttemptResult["items"][number],
  index: number,
  methodCorrectCalculationError: boolean,
  question: XingceLearnerQuestion,
  answer: XingceAnswerCheckpoint | undefined,
  materials: XingceLearnerMaterial[],
): XingceResultItemView {
  const category = factoredCategory(item, methodCorrectCalculationError);
  const timing = item.timingEvidence;
  if (!timing || !item.objectiveVerdict || !item.disposition || !item.confidenceEvidence) {
    throw new Error("行测逐题事实不完整。");
  }
  const material =
    question.materialId === null
      ? null
      : normalizeMaterial(
          materials.find((candidate) => candidate.materialId === question.materialId) ?? null,
        );
  return {
    questionId: item.questionId,
    positionNumber: index + 1,
    positionLabel: `第 ${String(index + 1)} 题`,
    stemContent: normalizeRichContent(
      question.stemContent,
      question.stem,
      `${question.questionId}-review-stem`,
    ),
    material,
    materialPosition: answer?.materialPosition ?? null,
    factChips: resultFactChips(item, question, answer),
    resultLabel: category.label,
    resultTone: category.tone,
    objectiveVerdict: item.objectiveVerdict,
    confidenceEvidence: item.confidenceEvidence,
    disposition: item.disposition,
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
    selectedAnswerText: answerText(item.selectedOptionIds, question),
    correctAnswerText: answerText(item.correctOptionIds, question),
    explanation: item.explanation ?? "这道题暂时没有解析。",
    explanationSource: explanationSourceLabel(item.explanationSource),
    draftText: answer?.draftText ?? "",
    annotations: (answer?.annotations ?? []).map((stroke) => ({
      strokeId: stroke.strokeId,
      surface: stroke.surface ?? "question",
      tool: stroke.tool,
      width: stroke.width,
      points: stroke.points.map((point) => ({
        x: point.x,
        y: point.y,
        // 契约增量 v1：逐点毫秒偏移随回放数据透传。
        ...(point.t !== undefined && point.t !== null ? { t: point.t } : {}),
        ...(point.p !== undefined && point.p !== null ? { p: point.p } : {}),
      })),
      atMs: stroke.atMs,
      // 契约增量 v1：擦除墓碑透传，回放在 replayMs >= erasedAtMs 时让笔画消失。
      ...(stroke.erasedAtMs !== undefined && stroke.erasedAtMs !== null
        ? { erasedAtMs: stroke.erasedAtMs }
        : {}),
      ...(stroke.anchor
        ? {
            anchor: {
              blockId: stroke.anchor.blockId,
              segmentIndex: stroke.anchor.segmentIndex,
              segmentText: stroke.anchor.segmentText,
            },
          }
        : {}),
    })),
    actionEvents: (answer?.actionEvents ?? []).map((entry) => ({
      atMs: entry.atMs,
      type: entry.type,
      ...(entry.sessionAtMs !== undefined && entry.sessionAtMs !== null
        ? { sessionAtMs: entry.sessionAtMs }
        : {}),
      ...(entry.optionId ? { optionId: entry.optionId } : {}),
      ...(entry.detail ? { detail: entry.detail } : {}),
    })),
    elapsedSeconds: timing.elapsedSeconds,
    trajectoryAvailable:
      (answer?.annotations?.length ?? 0) > 0 || (answer?.actionEvents?.length ?? 0) > 0,
  };
}

function resultQuestionGroups(
  items: readonly XingceResultItemView[],
): XingceResultQuestionGroupView[] {
  const definitions: Array<{
    code: XingceResultQuestionGroupView["code"];
    label: string;
    description: string;
    tone: XingceResultQuestionGroupView["tone"];
    match: (item: XingceResultItemView) => boolean;
  }> = [
    {
      code: "review",
      label: "优先回炉",
      description: "答错、未答或主动放弃，先把这些题问明白。",
      tone: "danger",
      match: (item) => item.objectiveVerdict !== "correct" || item.disposition === "active_abandon",
    },
    {
      code: "fragile",
      label: "答对但不稳",
      description: "这次猜对了，下一次还要验证是不是已经掌握。",
      tone: "warning",
      match: (item) =>
        item.objectiveVerdict === "correct" &&
        (item.confidenceEvidence === "low" || item.resultTone === "warning"),
    },
    {
      code: "pending",
      label: "答对待确认",
      description: "答案对了，但当时没有记录信心，不急着算真会。",
      tone: "neutral",
      match: (item) =>
        item.objectiveVerdict === "correct" && item.confidenceEvidence === "unreported",
    },
    {
      code: "mastered",
      label: "真会",
      description: "答对且有中高信心证据，继续复测再巩固。",
      tone: "success",
      match: (item) =>
        item.objectiveVerdict === "correct" &&
        (item.confidenceEvidence === "medium" || item.confidenceEvidence === "high") &&
        item.resultTone === "success",
    },
  ];
  return definitions.flatMap((definition) => {
    const grouped = items.filter(definition.match).map((item) => ({
      questionId: item.questionId,
      positionNumber: item.positionNumber,
    }));
    return grouped.length
      ? [
          {
            code: definition.code,
            label: definition.label,
            description: definition.description,
            tone: definition.tone,
            count: grouped.length,
            items: grouped,
          },
        ]
      : [];
  });
}

function processEvidence(items: readonly XingceResultItemView[]): XingceProcessEvidenceView {
  const liveStrokes = items.flatMap((item) =>
    item.annotations.filter((stroke) => stroke.erasedAtMs === undefined),
  );
  const anchoredSegments = new Set(
    liveStrokes.flatMap((stroke) =>
      stroke.anchor ? [`${stroke.anchor.blockId}:${String(stroke.anchor.segmentIndex)}`] : [],
    ),
  );
  const draftQuestionCount = items.filter((item) => item.draftText.trim().length > 0).length;
  const anchoredSegmentCount = anchoredSegments.size;
  const freeStrokeCount = liveStrokes.filter((stroke) => !stroke.anchor).length;
  return {
    hasEvidence: draftQuestionCount + anchoredSegmentCount + freeStrokeCount > 0,
    draftQuestionCount,
    anchoredSegmentCount,
    freeStrokeCount,
    scopeDescription:
      "文字草稿、已确认公式和原文标记会直接进入错因分析；自由笔迹会提取圈选、划线、划掉和擦改顺序。公式须经“手写公式”确认后才作为已识别内容；无法可靠辨认的笔迹会明确标记为未知，不会猜字或猜公式。",
  };
}

function explanationSourceLabel(source: string | null | undefined): string {
  if (source === "own_bank" || source === "question_bank") return "题库解析";
  if (source === "ai_rewrite") return "题库 AI 解析";
  return source ?? "题库解析";
}

function answerText(optionIds: string[], question: XingceLearnerQuestion): string {
  if (optionIds.length === 0) return "本题未选择答案";
  return optionIds
    .map((optionId) => {
      const option = question.options.find((candidate) => candidate.optionId === optionId);
      return option
        ? optionTextWithoutDuplicateLabel(optionId, option.text)
        : `选项 ${optionId} 的内容暂不可用`;
    })
    .join("；");
}

function optionTextWithoutDuplicateLabel(optionId: string, value: string): string {
  const normalized = value.trim();
  const escapedOptionId = optionId.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const prefix = new RegExp(`^${escapedOptionId}(?:\\s*[、.．。:：)）-]+\\s*|\\s+)`, "u");
  const withoutLabel = normalized.replace(prefix, "").trim();
  return withoutLabel || normalized;
}

function coachVoice(value: string): string {
  return value
    .trim()
    .replace(/该用户/gu, "你")
    .replace(/用户/gu, "你");
}

function resultFactChips(
  item: XingceAttemptResult["items"][number],
  question: XingceLearnerQuestion,
  answer: XingceAnswerCheckpoint | undefined,
): XingceResultItemView["factChips"] {
  const timing = item.timingEvidence;
  if (!timing || !item.confidenceEvidence) {
    throw new Error("行测逐题过程事实不完整。");
  }
  const optionLabels = new Map(
    question.options.map((option) => [option.optionId, option.label] as const),
  );
  const eliminated = (answer?.eliminatedOptionIds ?? []).map(
    (optionId) => optionLabels.get(optionId) ?? optionId,
  );
  const chips: XingceResultItemView["factChips"] = [
    {
      code: "elapsed",
      label: `用时 ${formatElapsed(timing.elapsedSeconds)}`,
      tone: timing.status === "advisory_exceeded" ? "warning" : "default",
    },
    {
      code: "confidence",
      label: `信心：${confidenceEvidenceLabel(item.confidenceEvidence)}`,
      tone: item.confidenceEvidence === "low" ? "warning" : "default",
    },
  ];
  if (eliminated.length > 0) {
    chips.push({
      code: "eliminated",
      label: `曾排除了 ${eliminated.join("、")}`,
      tone: "warning",
    });
  }
  // Figma 14:2「有改答记录」：来自不可变答案版本历史。
  if (typeof item.answerChangeCount === "number" && item.answerChangeCount > 0) {
    chips.push({
      code: "changed",
      label: "有改答记录",
      tone: "warning",
    });
  }
  if (answer?.marked) {
    chips.push({
      code: "marked",
      label: "标记存疑",
      tone: "warning",
    });
  }
  return chips;
}

/**
 * Figma 13:23 的五列指标：正确 / 用时 / 超时 / 改答 / 存疑。
 * 存疑来自持久化的标记，改答需要作答动作事件；老会话没有事件流时
 * 显示真实的空态「—」，不做推测。
 */
function resultMetrics(
  result: XingceAttemptResult,
  totalElapsedSeconds: number,
  answers: readonly XingceAnswerCheckpoint[],
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
  const markedCount = answers.filter((answer) => answer.marked).length;
  return [
    {
      code: "correct",
      label: "正确",
      value: `${String(result.correctCount)}/${String(result.totalCount)}`,
      tone: "default",
    },
    {
      code: "elapsed",
      label: "用时",
      value: formatElapsed(totalElapsedSeconds),
      tone: "default",
    },
    {
      code: "overtime",
      label: "超时",
      value: String(group.advisoryExceeded),
      tone: group.advisoryExceeded > 0 ? "warning" : "default",
    },
    {
      code: "changed",
      label: "改答",
      value: changedAnswerValue(result),
      tone: "default",
    },
    {
      code: "marked",
      label: "存疑",
      value: String(markedCount),
      tone: "default",
    },
  ];
}

/** 改答次数来自不可变答案版本历史；老结果缺失该事实时展示真实空态。 */
function changedAnswerValue(result: XingceAttemptResult): string {
  const value = result.changedAnswerCount;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? String(value) : "—";
}

/**
 * Figma 13:64「信心校准」：按 信心证据 × 客观结果 分桶，只展示出现过的桶。
 * 主动放弃不参与校准（既没有作答信心，也没有对错事实）。
 */
function confidenceCalibration(
  result: XingceAttemptResult,
): XingceResultView["confidenceCalibration"] {
  const buckets: Array<{
    code: XingceResultView["confidenceCalibration"][number]["code"];
    label: string;
    tone: XingceResultView["confidenceCalibration"][number]["tone"];
    match: (item: XingceAttemptResult["items"][number]) => boolean;
  }> = [
    {
      code: "confident_correct",
      label: "确定且正确",
      tone: "success",
      match: (item) => item.objectiveVerdict === "correct" && item.confidenceEvidence === "high",
    },
    {
      code: "hesitant_correct",
      label: "犹豫但正确",
      tone: "primary",
      match: (item) => item.objectiveVerdict === "correct" && item.confidenceEvidence === "medium",
    },
    {
      code: "guessed_correct",
      label: "基本靠猜但正确",
      tone: "warning",
      match: (item) => item.objectiveVerdict === "correct" && item.confidenceEvidence === "low",
    },
    {
      code: "confident_incorrect",
      label: "确定却错误",
      tone: "danger",
      match: (item) => item.objectiveVerdict !== "correct" && item.confidenceEvidence === "high",
    },
    {
      code: "hesitant_incorrect",
      label: "犹豫且错误",
      tone: "warning",
      match: (item) => item.objectiveVerdict !== "correct" && item.confidenceEvidence === "medium",
    },
    {
      code: "guessed_incorrect",
      label: "基本靠猜且错误",
      tone: "danger",
      match: (item) => item.objectiveVerdict !== "correct" && item.confidenceEvidence === "low",
    },
  ];
  const participating = result.items.filter((item) => item.disposition !== "active_abandon");
  return buckets
    .map((bucket) => ({
      code: bucket.code,
      label: bucket.label,
      value: participating.filter(bucket.match).length,
      tone: bucket.tone,
    }))
    .filter((bucket) => bucket.value > 0);
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

function presentGroupSummary(
  summary: XingceGroupSummary,
  totalCount: number,
): XingceGroupSummaryView {
  const groupLabel = `${String(totalCount)} 题训练`;
  const outcomeCopy = {
    attained: {
      label: `${groupLabel}已达成`,
      tone: "success" as const,
      description: `全部 ${String(totalCount)} 题均有客观答对且中高信心证据。`,
    },
    not_attained: {
      label: `${groupLabel}已完成`,
      tone: "warning" as const,
      description: "答对不等于真会；猜对、错题和未答会分开保留。",
    },
    interrupted: {
      label: `${groupLabel}已交卷`,
      tone: "neutral" as const,
      description: `本次 ${String(totalCount)} 题均未作答，不自动记为答错。`,
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
