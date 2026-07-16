import type {
  XingceBriefingMetric,
  XingceBriefingMode,
  XingceTaskBriefing,
} from "../../../api/xingce-learning";
import { registeredTaskNavigationTarget } from "../../../app/navigation/routes";

const requiredMetrics = new Set<XingceBriefingMetric>([
  "answer_verdict",
  "confidence_evidence",
  "timing_evidence",
  "reflection",
  "cause_decision",
]);

export class XingceBriefingContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "XingceBriefingContractError";
  }
}

export function parseXingceBriefingMode(value: string | undefined): XingceBriefingMode | null {
  const normalized = value?.trim();
  return normalized === "baseline" || normalized === "targeted" || normalized === "verification"
    ? normalized
    : null;
}

export function isValidBriefingPlanTaskId(value: string | undefined): value is string {
  if (value === undefined) return false;
  const normalized = value.trim();
  return normalized.length > 0 && normalized.length <= 160 && !/[\s/#?]/u.test(normalized);
}

export function assertXingceTaskBriefing(
  value: XingceTaskBriefing,
  expectedPlanTaskId: string,
): XingceTaskBriefing {
  const contractVersion = value.contractVersion;
  if (contractVersion !== "xingce-task-briefing@v1") {
    throw new XingceBriefingContractError("任务说明合同版本不受当前客户端支持。");
  }
  if (
    value.planTaskId !== expectedPlanTaskId ||
    !value.planVersionId.trim() ||
    !value.taskDefinitionVersion.trim()
  ) {
    throw new XingceBriefingContractError("任务说明没有绑定当前计划与任务定义版本。");
  }
  const rawBriefing = value as unknown as Record<string, unknown>;
  const mode = rawBriefing.mode;
  if (mode !== "baseline" && mode !== "targeted" && mode !== "verification") {
    throw new XingceBriefingContractError("任务说明模式无效。");
  }
  const questionCount = value.questionCount;
  if (!value.title.trim() || !value.reason.trim() || questionCount !== 3) {
    throw new XingceBriefingContractError("任务范围、原因或固定题数不完整。");
  }
  const timingBehavior = value.timing.behavior;
  const rawTiming = value.timing as unknown as Record<string, unknown>;
  if (
    timingBehavior !== "advisory" ||
    !Number.isInteger(value.timing.targetSeconds) ||
    value.timing.targetSeconds < 1 ||
    !Number.isInteger(value.timing.perQuestionTargetSeconds) ||
    value.timing.perQuestionTargetSeconds < 1 ||
    rawTiming.overtimeIsRecorded !== true
  ) {
    throw new XingceBriefingContractError("任务建议时长合同无效。");
  }
  const metrics = value.completion.requiredMetrics;
  const rawCompletion = value.completion as unknown as Record<string, unknown>;
  if (
    !value.completion.evidenceType.trim() ||
    rawCompletion.stableMasteryRequiresDelayedVerification !== true ||
    metrics.length !== requiredMetrics.size ||
    new Set(metrics).size !== metrics.length ||
    metrics.some((metric) => !requiredMetrics.has(metric))
  ) {
    throw new XingceBriefingContractError("任务验收证据合同不完整。");
  }
  const rawAbandonment = value.abandonment as unknown as Record<string, unknown>;
  const abandonmentSemantics = value.abandonment.semantics;
  if (
    rawAbandonment.allowed !== true ||
    abandonmentSemantics !== "active_abandon_is_not_automatic_error" ||
    rawAbandonment.requiresExplicitAction !== true
  ) {
    throw new XingceBriefingContractError("主动放弃语义合同无效。");
  }
  if (!value.validUntil.trim() || Number.isNaN(new Date(value.validUntil).getTime())) {
    throw new XingceBriefingContractError("任务说明有效期无效。");
  }
  const verificationStage = rawBriefing.verificationStage;
  const sourceMasteryVersionId = rawBriefing.sourceMasteryVersionId;
  if (mode === "verification") {
    if (
      (verificationStage !== "immediate" &&
        verificationStage !== "first_review" &&
        verificationStage !== "delayed_review") ||
      typeof sourceMasteryVersionId !== "string" ||
      !sourceMasteryVersionId.trim()
    ) {
      throw new XingceBriefingContractError("复测任务缺少阶段或来源掌握版本。");
    }
  } else if (verificationStage !== null || sourceMasteryVersionId !== null) {
    throw new XingceBriefingContractError("非复测任务不应绑定复测掌握版本。");
  }
  return value;
}

export function xingcePracticeUrl(briefing: XingceTaskBriefing): string {
  const url =
    `/pages/practice/index?mode=${encodeURIComponent(briefing.mode)}` +
    `&planTaskId=${encodeURIComponent(briefing.planTaskId)}`;
  if (!registeredTaskNavigationTarget(url)) {
    throw new XingceBriefingContractError("服务端任务说明无法生成受支持的作答入口。");
  }
  return url;
}

export function briefingExpired(briefing: XingceTaskBriefing, now = Date.now()): boolean {
  return new Date(briefing.validUntil).getTime() <= now;
}
