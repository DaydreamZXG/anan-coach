import type {
  AppDomainsAbilityEstimationSchemasScoreRange as AbilityScoreRange,
  OnboardingResultProjection,
} from "../../api/generated/types.gen";

export type OnboardingResultAction = OnboardingResultProjection["nextAction"];

export interface OnboardingResultView {
  status: OnboardingResultProjection["status"];
  eyebrow: string;
  title: string;
  description: string;
  action: OnboardingResultAction;
  actionLabel: string;
  goalLabel: string;
  gapLabel: string;
  gapCaption: string;
  currentTotalLabel: string;
  targetTotalLabel: string;
  xingceLabel: string;
  essayLabel: string;
  evidenceLabel: string;
  weekdayBudgetLabel: string;
  weekendBudgetLabel: string;
  rhythmLabel: string;
  validityLabel: string;
}

export function onboardingResultView(result: OnboardingResultProjection): OnboardingResultView {
  const ability = result.abilityEstimate;
  const evidenceCount = ability?.evidenceSummary.evidenceCount ?? 0;
  const base = {
    status: result.status,
    action: result.nextAction,
    goalLabel: result.primaryGoal
      ? `${result.primaryGoal.departmentName} · ${result.primaryGoal.positionName}`
      : "尚未选择主目标岗位",
    gapLabel: "暂不可计算",
    gapCaption: "岗位分差要求同年、有效且行测与申论两科完整",
    currentTotalLabel: scoreRangeLabel(ability?.total),
    targetTotalLabel: result.primaryGoal
      ? targetRangeLabel(result.primaryGoal.targetScore)
      : "待选择目标",
    xingceLabel: scoreRangeLabel(ability?.xingce),
    essayLabel: scoreRangeLabel(ability?.shenlun),
    evidenceLabel:
      evidenceCount > 0 ? `${String(evidenceCount)} 条已确认成绩证据` : "尚无正式成绩证据",
    weekdayBudgetLabel: `${String(result.weeklyPreference.weekdayMinutes)} 分钟`,
    weekendBudgetLabel: `${String(result.weeklyPreference.weekendMinutes)} 分钟`,
    rhythmLabel: `${weekdayLabel(result.weeklyPreference.mockExamDay)}模考 · ${weekdayLabel(result.weeklyPreference.fixedRestDay)}恢复`,
    validityLabel: `本结果有效至 ${dateLabel(result.validUntil)}`,
  } as const;

  switch (result.status) {
    case "evidence_sufficient": {
      if (!result.scoreGap || !result.primaryGoal || !ability?.total) {
        throw new Error("Evidence-sufficient onboarding result is missing its dual-track basis");
      }
      const recommendedGap = result.scoreGap.recommended;
      return {
        ...base,
        eyebrow: "双科证据完整 · 可以计算分差",
        title:
          recommendedGap > 0
            ? `距目标建议线约 ${numberLabel(recommendedGap)} 分`
            : "当前区间已覆盖目标建议线",
        description:
          "这是基于当前行测、申论与岗位样本的区间判断；新增模考或正式成绩后会生成新版本。",
        actionLabel: "进入今日工作台",
        gapLabel: signedNumberLabel(recommendedGap),
        gapCaption: `${numberLabel(result.scoreGap.lower)} ～ ${numberLabel(result.scoreGap.upper)} 分（正数表示目标高于当前）`,
      };
    }
    case "baseline_required":
      return {
        ...base,
        eyebrow: "双科能力证据尚未就绪",
        title: baselineTitle(result),
        description:
          "先补充同一口径下的行测与申论真实成绩。单科分、旧档案分和单篇 AI 训练估分都不会被拿来凑总分。",
        actionLabel: baselineActionLabel(result.nextAction),
      };
    case "position_selection_required":
      return {
        ...base,
        eyebrow: "资格范围已生成 · 目标待本人选择",
        title: "先选一个主目标，再计算你离它多远",
        description:
          "系统已经找到当前已确认可报的岗位，但不会替你作最终选择。目标变化后，分差和计划都会按新版本重算。",
        actionLabel: "选择主目标岗位",
      };
    case "no_eligible_position":
      return {
        ...base,
        eyebrow: "当前没有已确认可报岗位",
        title: "先核验资格缺口，不编造岗位分差",
        description:
          "可能是本年度岗位尚无匹配，也可能仍有条件待确认。系统会保留逐条岗位原文和你的资格事实供核验。",
        actionLabel: "查看资格与岗位依据",
      };
  }
}

function baselineActionLabel(action: OnboardingResultAction): string {
  return {
    enter_workspace: "进入今日工作台",
    record_baseline: "开始双科起点诊断",
    choose_position: "选择主目标岗位",
    review_exam_briefing: "先看考情，再开始诊断",
    import_prior_evidence: "导入已有成绩与学习资料",
    start_retake_recovery: "建立二战失分回收计划",
  }[action];
}

function baselineTitle(result: OnboardingResultProjection): string {
  if (result.reasonCode === "ability_partial") {
    const missing = result.abilityEstimate?.evidenceSummary.missingComponents ?? [];
    if (missing.length === 1) {
      return `还缺${missing[0] === "xingce" ? "行测" : "申论"}成绩，暂不计算总分差`;
    }
  }
  if (result.reasonCode === "ability_stale") return "双科成绩已过有效期，需要新证据";
  return "先建立行测与申论的共同起点";
}

function scoreRangeLabel(value: AbilityScoreRange | null | undefined): string {
  return value ? `${numberLabel(value.lower)}–${numberLabel(value.upper)}` : "证据不足";
}

function targetRangeLabel(value: { lowerBound: number; upperBound: number }): string {
  return `${numberLabel(value.lowerBound)}–${numberLabel(value.upperBound)}`;
}

function signedNumberLabel(value: number): string {
  if (value > 0) return `+${numberLabel(value)} 分`;
  if (value < 0) return `${numberLabel(value)} 分`;
  return "0 分";
}

function numberLabel(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1).replace(/\.0$/u, "");
}

function dateLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "服务端标记时间";
  return `${String(date.getFullYear())}.${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

function weekdayLabel(
  value: OnboardingResultProjection["weeklyPreference"]["mockExamDay"],
): string {
  return {
    monday: "周一",
    tuesday: "周二",
    wednesday: "周三",
    thursday: "周四",
    friday: "周五",
    saturday: "周六",
    sunday: "周日",
  }[value];
}
