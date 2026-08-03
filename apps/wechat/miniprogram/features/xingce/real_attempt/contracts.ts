import type {
  XingceAnswerCheckpoint,
  XingceAttemptResult,
  XingceItemInterpretation,
  XingceResultItem,
  XingceTaskMode,
  XingceTaskSession,
} from "../../../api/xingce-learning";
import type { TodayDashboard } from "../../../api/generated/types.gen";

export type XingcePlanStage =
  "objective_baseline" | "objective_practice" | "objective_verification";

export const xingceReflectionTags = [
  "knowledge_gap",
  "misread",
  "method_error",
  "calculation_error",
  "time_pressure",
  "guessed",
  "other",
] as const;

export type XingceReflectionTag = (typeof xingceReflectionTags)[number];

export const xingceReflectionTagLabels: Record<XingceReflectionTag, string> = {
  knowledge_gap: "知识盲区",
  misread: "理解偏差",
  method_error: "方法错误",
  calculation_error: "计算失误",
  time_pressure: "时间压力",
  guessed: "猜测作答",
  other: "其他",
};

/**
 * 用户改写错因时可选的结构化 8 码，与后端已发布错因分类
 * （xingce-cause-taxonomy@v1）一一对应；不含 'user_replacement' 自身。
 * 选码可选：不选码时改写仍按自由文本单桶处理，旧行为不变。
 */
export const xingceReplacementCauseCodes = [
  "stem_misread",
  "knowledge_gap",
  "method_selection",
  "calculation_error",
  "time_pressure",
  "guessing",
  "active_abandon",
  "other",
] as const;

export type XingceReplacementCauseCode = (typeof xingceReplacementCauseCodes)[number];

/** 人话文案：按 taxonomy 定义翻译，不加诊断口吻。 */
export const xingceReplacementCauseCodeLabels: Record<XingceReplacementCauseCode, string> = {
  stem_misread: "题干看漏看错",
  knowledge_gap: "知识点没掌握",
  method_selection: "方法没选对",
  calculation_error: "会做但算错了",
  time_pressure: "时间太紧太仓促",
  guessing: "凭感觉猜的",
  active_abandon: "主动放弃这题",
  other: "都不太像",
};

export function isXingceReplacementCauseCode(value: string): value is XingceReplacementCauseCode {
  return xingceReplacementCauseCodes.includes(value as XingceReplacementCauseCode);
}

const allowedModes = new Set<XingceTaskMode>(["practice", "targeted", "baseline", "verification"]);

export function parseXingceTaskMode(value: string | undefined): XingceTaskMode | null {
  const normalized = value?.trim();
  return normalized && allowedModes.has(normalized as XingceTaskMode)
    ? (normalized as XingceTaskMode)
    : null;
}

export function planStageForXingceMode(mode: XingceTaskMode): XingcePlanStage {
  if (mode === "baseline") return "objective_baseline";
  if (mode === "verification") return "objective_verification";
  return "objective_practice";
}

export function isValidPlanTaskId(value: string | undefined): value is string {
  if (value === undefined) return false;
  const normalized = value.trim();
  return normalized.length > 0 && normalized.length <= 160 && !/[\s/#?]/u.test(normalized);
}

export class XingcePlanResolutionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "XingcePlanResolutionError";
  }
}

/**
 * Resolves only an owned, current Today work item. The page never guesses from a
 * stage alone and never starts a legacy/local practice task.
 */
export function resolveXingcePlanTaskId(
  dashboard: TodayDashboard,
  mode: XingceTaskMode,
  requestedPlanTaskId?: string,
): string {
  const stage = planStageForXingceMode(mode);
  const candidates = dashboard.tasks.filter((task) => {
    if (
      task.track !== "xingce" ||
      task.stage !== stage ||
      task.status === "complete" ||
      task.status === "abandoned"
    ) {
      return false;
    }
    const target = parsePracticeStartUrl(task.startUrl);
    return target !== null && target.mode === mode;
  });

  const requested = requestedPlanTaskId?.trim();
  if (requested) {
    if (!isValidPlanTaskId(requested)) {
      throw new XingcePlanResolutionError("计划任务标识无效，已停止创建行测会话。");
    }
    const exact = candidates.filter((task) => task.id === requested);
    if (exact.length !== 1) {
      throw new XingcePlanResolutionError("该行测任务已不在当前 Today 计划中，请返回今日页刷新。");
    }
    return exact[0]?.id ?? requested;
  }
  if (candidates.length !== 1) {
    throw new XingcePlanResolutionError(
      candidates.length === 0
        ? "当前 Today 计划没有可执行的同阶段行测任务。"
        : "当前 Today 计划存在多个同阶段行测任务，无法安全判断要开始哪一个。",
    );
  }
  const candidate = candidates[0];
  if (!candidate) throw new XingcePlanResolutionError("当前行测任务无法解析。");
  return candidate.id;
}

export function emptyXingceAnswer(questionId: string): XingceAnswerCheckpoint {
  return {
    questionId,
    selectedOptionIds: [],
    eliminatedOptionIds: [],
    confidence: null,
    marked: false,
    skipped: false,
    elapsedSeconds: 0,
    draftText: "",
  };
}

export function assertLearnerSafeSession(session: XingceTaskSession): XingceTaskSession {
  if (!session.sessionId.trim() || !session.planTaskId.trim()) {
    throw new Error("行测会话缺少稳定身份。");
  }
  if (!Number.isInteger(session.revision) || session.revision < 1) {
    throw new Error("本次行测任务数据无效，请重新进入。");
  }
  const questions = session.bundle.questions;
  assertNoLearnerUnsafeKeys(session.bundle);
  if (questions.length === 0) throw new Error("没有拿到可作答题目。");
  const questionIds = questions.map((item) => item.questionId);
  if (new Set(questionIds).size !== questionIds.length) {
    throw new Error("题组存在重复题目。");
  }
  if (!questionIds.includes(session.currentQuestionId)) {
    throw new Error("当前题不在题组中。");
  }
  for (const question of questions) {
    const raw = question as unknown as Record<string, unknown>;
    if (
      "answer" in raw ||
      "correctAnswer" in raw ||
      "correctOptionIds" in raw ||
      "explanation" in raw
    ) {
      throw new Error("作答前题面含有答案或解析，页面已停止展示。");
    }
    if (!question.contentVersionId.trim() || question.options.length < 2) {
      throw new Error("题目或选项不完整，请重新加载。");
    }
    const optionIds = question.options.map((option) => option.optionId);
    if (
      optionIds.some((optionId) => !optionId.trim()) ||
      new Set(optionIds).size !== optionIds.length
    ) {
      throw new Error("题目选项标识不完整。");
    }
    if (
      question.materialId !== null &&
      !session.bundle.materials.some((material) => material.materialId === question.materialId)
    ) {
      throw new Error("题目引用的材料不存在。");
    }
  }
  const answerIds = session.answers.map((item) => item.questionId);
  if (new Set(answerIds).size !== answerIds.length) {
    throw new Error("作答进度存在重复题目。");
  }
  if (answerIds.some((questionId) => !questionIds.includes(questionId))) {
    throw new Error("作答进度与题组不匹配。");
  }
  if (session.result === null && session.review !== null) {
    throw new Error("未交卷会话不应包含复盘结果。");
  }
  if (session.state === "answering" && (session.result !== null || session.review !== null)) {
    throw new Error("作答中会话不应提前暴露结果或复盘。");
  }
  if (session.state !== "answering" && (session.result === null || session.review === null)) {
    throw new Error("已交卷会话缺少客观结果或复盘标识。");
  }
  if (session.result) {
    if (isFactoredXingceResult(session.result)) {
      assertFactoredXingceResult(
        session.result,
        questionIds,
        session.review?.reflection?.itemInterpretations ?? [],
      );
      assertVerificationOutcome(session);
    } else {
      assertLegacyResultIsNotMixed(session);
    }
  }
  if (session.review) {
    if (!Number.isInteger(session.review.revision) || session.review.revision < 1) {
      throw new Error("复盘记录无效，请重新进入。");
    }
  }
  if (session.state === "completed" && session.completion === null) {
    throw new Error("已完成会话缺少计划结算记录。");
  }
  return session;
}

export function isFactoredXingceResult(result: XingceAttemptResult): boolean {
  return result.contractVersion === "xingce-attempt-result@v2";
}

export function assertFactoredXingceResult(
  result: XingceAttemptResult,
  questionIds: readonly string[],
  itemInterpretations: readonly XingceItemInterpretation[] = [],
): XingceAttemptResult {
  if (result.contractVersion !== "xingce-attempt-result@v2" || !result.groupSummary) {
    throw new Error("行测结果不是已确认的事实拆分。");
  }
  const expectedCount = questionIds.length;
  if (
    expectedCount < 1 ||
    result.totalCount !== expectedCount ||
    result.items.length !== expectedCount
  ) {
    throw new Error("行测结果必须绑定本次完整题目集。");
  }
  const itemIds = result.items.map((item) => item.questionId);
  if (
    new Set(itemIds).size !== itemIds.length ||
    itemIds.some((questionId) => !questionIds.includes(questionId))
  ) {
    throw new Error("行测结果题目与确认题组不匹配。");
  }
  result.items.forEach(assertFactoredResultItem);
  assertInterpretations(itemInterpretations, result.items);
  const summary = result.groupSummary;
  const genuineCorrect = result.items.filter(
    (item) =>
      item.objectiveVerdict === "correct" &&
      (item.confidenceEvidence === "medium" || item.confidenceEvidence === "high"),
  ).length;
  const guessedCorrect = result.items.filter(
    (item) => item.objectiveVerdict === "correct" && item.confidenceEvidence === "low",
  ).length;
  const activeAbandon = result.items.filter((item) => item.disposition === "active_abandon").length;
  const advisoryExceeded = result.items.filter(
    (item) => item.timingEvidence?.status === "advisory_exceeded",
  ).length;
  const methodCorrectCalculationError = itemInterpretations.length;
  if (
    summary.genuineCorrect !== genuineCorrect ||
    summary.guessedCorrect !== guessedCorrect ||
    summary.activeAbandon !== activeAbandon ||
    summary.advisoryExceeded !== advisoryExceeded ||
    summary.methodCorrectCalculationError !== methodCorrectCalculationError
  ) {
    throw new Error("行测结果计数与逐题事实不一致。");
  }
  const expectedOutcome =
    activeAbandon === expectedCount
      ? "interrupted"
      : genuineCorrect === expectedCount
        ? "attained"
        : "not_attained";
  if (summary.outcome !== expectedOutcome) {
    throw new Error("行测结论与逐题事实不一致。");
  }
  return result;
}

export function methodInterpretationEligibleQuestionIds(result: XingceAttemptResult): string[] {
  return result.items
    .filter((item) => item.objectiveVerdict === "incorrect" && item.disposition === "answered")
    .map((item) => item.questionId);
}

function assertFactoredResultItem(item: XingceResultItem): void {
  if (
    item.factContractVersion !== "xingce-result-facts@v1" ||
    item.objectiveVerdict == null ||
    item.disposition == null ||
    item.confidenceEvidence == null ||
    item.timingEvidence == null
  ) {
    throw new Error("行测逐题结果缺少客观、处置、信心或计时事实。");
  }
  if (
    item.disposition === "active_abandon" &&
    (item.objectiveVerdict !== "unanswered" || item.selectedOptionIds.length > 0)
  ) {
    throw new Error("主动放弃不能被混成答错或已选答案。");
  }
  const timing = item.timingEvidence;
  if (
    !Number.isInteger(timing.elapsedSeconds) ||
    timing.elapsedSeconds < 0 ||
    !Number.isInteger(timing.advisorySeconds) ||
    timing.advisorySeconds < 1 ||
    (timing.status === "advisory_exceeded") !== timing.elapsedSeconds > timing.advisorySeconds
  ) {
    throw new Error("行测逐题建议用时事实不一致。");
  }
}

function assertInterpretations(
  interpretations: readonly XingceItemInterpretation[],
  items: readonly XingceResultItem[],
): void {
  if (interpretations.length > 3) throw new Error("单次复盘最多标记 3 条逐题解读。");
  const ids = interpretations.map((item) => item.questionId);
  const eligible = new Set(
    items
      .filter((item) => item.objectiveVerdict === "incorrect" && item.disposition === "answered")
      .map((item) => item.questionId),
  );
  if (
    new Set(ids).size !== ids.length ||
    interpretations.some(
      (item) =>
        (item as unknown as Record<string, unknown>).interpretation !==
          "method_correct_calculation_error" || !eligible.has(item.questionId),
    )
  ) {
    throw new Error("方法正确但计算错误只能绑定本次已作答的客观错题。");
  }
}

function assertVerificationOutcome(session: XingceTaskSession): void {
  const outcome = session.verificationOutcome;
  if (session.mode !== "verification") {
    if (outcome) throw new Error("非复测会话不应携带复测结论。");
    return;
  }
  if (!outcome || !outcome.sourceMasteryVersionId.trim()) {
    throw new Error("复测结果缺少确认后的掌握记录或结论。");
  }
  const allAnsweredCorrect = session.result?.items.every(
    (item) => item.objectiveVerdict === "correct" && item.disposition === "answered",
  );
  if (
    (outcome.outcome === "passed" && (outcome.evidenceState !== "valid" || !allAnsweredCorrect)) ||
    (outcome.outcome === "not_passed" &&
      (outcome.evidenceState !== "valid" || allAnsweredCorrect)) ||
    (outcome.outcome === "disputed_or_invalidated" && outcome.evidenceState === "valid")
  ) {
    throw new Error("复测结论与客观结果或证据状态不一致。");
  }
}

function assertLegacyResultIsNotMixed(session: XingceTaskSession): void {
  const result = session.result;
  if (!result) return;
  const hasNewFacts =
    result.groupSummary != null ||
    result.items.some(
      (item) =>
        item.factContractVersion != null ||
        item.objectiveVerdict != null ||
        item.disposition != null ||
        item.confidenceEvidence != null ||
        item.timingEvidence != null,
    );
  if (hasNewFacts || session.verificationOutcome !== null) {
    throw new Error("历史行测结果混入了未确认的新事实。");
  }
}

const learnerUnsafeKeys = new Set([
  "answer",
  "correctanswer",
  "correctoptionids",
  "explanation",
  "originalanalysis",
  "preferredanalysis",
  "storedanalysis",
]);

function assertNoLearnerUnsafeKeys(value: unknown): void {
  if (Array.isArray(value)) {
    value.forEach(assertNoLearnerUnsafeKeys);
    return;
  }
  if (typeof value !== "object" || value === null) return;
  for (const [key, nested] of Object.entries(value)) {
    const normalized = key.replaceAll("_", "").toLowerCase();
    if (learnerUnsafeKeys.has(normalized)) {
      throw new Error("作答前题面含有答案或解析，页面已停止展示。");
    }
    assertNoLearnerUnsafeKeys(nested);
  }
}

export function normalizeSessionAnswers(session: XingceTaskSession): XingceAnswerCheckpoint[] {
  const byQuestion = new Map(session.answers.map((answer) => [answer.questionId, answer]));
  return session.bundle.questions.map((question) => {
    const answer = byQuestion.get(question.questionId) ?? emptyXingceAnswer(question.questionId);
    return {
      ...answer,
      selectedOptionIds: [...answer.selectedOptionIds],
      eliminatedOptionIds: [...answer.eliminatedOptionIds],
      ...(answer.materialPosition ? { materialPosition: { ...answer.materialPosition } } : {}),
    };
  });
}

export function isXingceReflectionTag(value: string): value is XingceReflectionTag {
  return xingceReflectionTags.includes(value as XingceReflectionTag);
}

function parsePracticeStartUrl(startUrl: string): { mode: XingceTaskMode } | null {
  const normalized = startUrl.trim();
  const [path, rawQuery = ""] = normalized.split("?", 2);
  if (path !== "/pages/practice/index") return null;
  const values = new Map<string, string>();
  try {
    for (const pair of rawQuery.split("&").filter(Boolean)) {
      const [rawKey = "", rawValue = ""] = pair.split("=", 2);
      const key = decodeURIComponent(rawKey);
      if (!key || values.has(key)) return null;
      values.set(key, decodeURIComponent(rawValue));
    }
  } catch {
    return null;
  }
  const mode = parseXingceTaskMode(values.get("mode"));
  return mode ? { mode } : null;
}
