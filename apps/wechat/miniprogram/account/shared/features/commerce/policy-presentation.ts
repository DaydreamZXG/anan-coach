import type { CommercialPolicySnapshot } from "../../../../api/generated/types.gen";

export type CommercialPolicyView = CommercialPolicySnapshot & { contentLabel: string };

const requiredPolicyKinds: CommercialPolicySnapshot["policyKind"][] = [
  "service_terms",
  "refund",
  "process_reward",
  "marketing",
];

export function presentCommercialPolicies(
  policies: CommercialPolicySnapshot[],
): CommercialPolicyView[] {
  const kinds = new Set(policies.map((item) => item.policyKind));
  if (
    policies.length !== requiredPolicyKinds.length ||
    kinds.size !== requiredPolicyKinds.length ||
    !requiredPolicyKinds.every((kind) => kinds.has(kind))
  ) {
    throw new Error("当前商品没有完整且唯一的四项固化政策，已停止售卖。");
  }
  return policies.map((policy) => ({
    ...policy,
    contentLabel: commercialPolicySummary(policy),
  }));
}

export function commercialPolicySummary(policy: CommercialPolicySnapshot): string {
  const content = requiredRecord(policy.content, `${policy.policyKind} policy`);
  if (policy.policyKind === "refund") {
    const windowDays = requiredPositiveNumber(content.windowDays, "refund.windowDays");
    requiredFalse(content.scoreEvidenceRequired, "refund.scoreEvidenceRequired");
    requiredFalse(content.interviewRequired, "refund.interviewRequired");
    requiredFalse(content.completionRateRequired, "refund.completionRateRequired");
    return `购买后 ${String(windowDays)} 个自然日内认为不适合可申请退款；不要求成绩、访谈或任务完成率。`;
  }
  if (policy.policyKind === "process_reward") {
    const effectiveDay = requiredRecord(content.effectiveDay, "processReward.effectiveDay");
    const weeklyReflection = requiredRecord(
      content.weeklyReflection,
      "processReward.weeklyReflection",
    );
    const coreCompletion = requiredRecord(content.coreCompletion, "processReward.coreCompletion");
    const effectiveCount = requiredPositiveNumber(
      effectiveDay.requiredCount,
      "processReward.effectiveDay.requiredCount",
    );
    const rewardDays = requiredPositiveNumber(
      effectiveDay.rewardServiceDays,
      "processReward.effectiveDay.rewardServiceDays",
    );
    const reflectionCount = requiredPositiveNumber(
      weeklyReflection.requiredCount,
      "processReward.weeklyReflection.requiredCount",
    );
    const rewardMinor = requiredPositiveNumber(
      weeklyReflection.rewardMinorUnits,
      "processReward.weeklyReflection.rewardMinorUnits",
    );
    const completionRatio = requiredRatio(
      coreCompletion.requiredRatio,
      "processReward.coreCompletion.requiredRatio",
    );
    return `${String(effectiveCount)} 个有效学习日赠 ${String(rewardDays)} 天；${String(reflectionCount)} 次周复盘返 ${moneyLabel(rewardMinor)}；完成 ${String(Math.round(completionRatio * 100))}% 核心任务解锁专项诊断。`;
  }
  if (policy.policyKind === "marketing") {
    const prohibited = requiredStringArray(content.prohibited, "marketing.prohibited");
    return `禁止宣传：${prohibited.join("、")}。`;
  }
  const serviceDays = requiredPositiveNumber(content.serviceDays, "serviceTerms.serviceDays");
  requiredFalse(content.automaticRenewal, "serviceTerms.automaticRenewal");
  return `单次购买 ${String(serviceDays)} 天，不自动续费；实际服务内容以订单固化清单为准。`;
}

function requiredRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} 结构无效`);
  }
  return value as Record<string, unknown>;
}

function requiredPositiveNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new Error(`${label} 必须是正数`);
  }
  return value;
}

function requiredRatio(value: unknown, label: string): number {
  const result = requiredPositiveNumber(value, label);
  if (result > 1) throw new Error(`${label} 不能大于 1`);
  return result;
}

function requiredFalse(value: unknown, label: string): void {
  if (value !== false) throw new Error(`${label} 必须明确为 false`);
}

function requiredStringArray(value: unknown, label: string): string[] {
  if (
    !Array.isArray(value) ||
    value.length === 0 ||
    value.some((item) => typeof item !== "string" || !item.trim())
  ) {
    throw new Error(`${label} 必须是非空字符串列表`);
  }
  return value.map((item) => String(item));
}

function moneyLabel(minor: number): string {
  return `¥${(minor / 100).toFixed(2)}`;
}
