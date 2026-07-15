import type {
  XingceAnswerCheckpoint,
  XingceResultCode,
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
  if (mode === "verification") {
    throw new XingcePlanResolutionError("间隔验证不属于本次真实行测训练交付，不能伪装为专项训练。");
  }
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

export function resultRequiresCauseDecision(resultCode: XingceResultCode): boolean {
  return resultCode !== "correct";
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
    throw new Error("服务端行测会话缺少稳定身份。");
  }
  if (!Number.isInteger(session.revision) || session.revision < 1) {
    throw new Error("服务端行测会话版本无效。");
  }
  const questions = session.bundle.questions;
  if (questions.length === 0) throw new Error("服务端没有返回可作答题目。");
  const questionIds = questions.map((item) => item.questionId);
  if (new Set(questionIds).size !== questionIds.length) {
    throw new Error("服务端题组存在重复题目。");
  }
  if (!questionIds.includes(session.currentQuestionId)) {
    throw new Error("服务端当前题不在题组中。");
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
      throw new Error("服务端题面版本或选项不完整。");
    }
    const optionIds = question.options.map((option) => option.optionId);
    if (
      optionIds.some((optionId) => !optionId.trim()) ||
      new Set(optionIds).size !== optionIds.length
    ) {
      throw new Error("服务端题目选项标识不完整。");
    }
    if (
      question.materialId !== null &&
      !session.bundle.materials.some((material) => material.materialId === question.materialId)
    ) {
      throw new Error("服务端题目引用的材料不存在。");
    }
  }
  const answerIds = session.answers.map((item) => item.questionId);
  if (new Set(answerIds).size !== answerIds.length) {
    throw new Error("服务端作答断点存在重复题目。");
  }
  if (answerIds.some((questionId) => !questionIds.includes(questionId))) {
    throw new Error("服务端作答断点与题组不匹配。");
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
  if (session.review) {
    if (!Number.isInteger(session.review.revision) || session.review.revision < 1) {
      throw new Error("复盘版本无效。");
    }
  }
  if (session.state === "completed" && session.completion === null) {
    throw new Error("已完成会话缺少计划结算回执。");
  }
  return session;
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
