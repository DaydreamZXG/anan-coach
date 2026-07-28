import type {
  AppDomainsAbilityEstimationSchemasScoreRange as AbilityScoreRange,
  OnboardingResultProjection,
} from "../../api/generated/types.gen";
import { formatStudyDuration } from "./study-preference";

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
  confidenceLabel: string;
  confidenceTone: "high" | "calibrating" | "review";
  reliableJudgment: string;
  riskJudgment: string;
  positionNotice: string;
  primarySuggestionTitle: string;
  primarySuggestionCopy: string;
  secondarySuggestionTitle: string;
  secondarySuggestionCopy: string;
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
    weekdayBudgetLabel: formatStudyDuration(result.weeklyPreference.weekdayMinutes),
    weekendBudgetLabel: formatStudyDuration(result.weeklyPreference.weekendMinutes),
    rhythmLabel: `${weekdayLabel(result.weeklyPreference.mockExamDay)}模考 · ${weekdayLabel(result.weeklyPreference.fixedRestDay)}恢复`,
    validityLabel: `本结果有效至 ${dateLabel(result.validUntil)}`,
    positionNotice:
      "当前判断只基于已确认的资格事实、岗位规则与成绩证据；职位表、岗位条件或成绩证据变化后会重新生成，不代表录取承诺。",
    secondarySuggestionTitle: "查看档案与判断依据",
    secondarySuggestionCopy: "核对已确认信息和仍需补充的内容",
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
        description: "这是基于当前行测、申论与岗位样本的区间判断；新增模考或正式成绩后会重新计算。",
        actionLabel: "进入今日工作台",
        gapLabel: signedNumberLabel(recommendedGap),
        gapCaption: `${numberLabel(result.scoreGap.lower)} ～ ${numberLabel(result.scoreGap.upper)} 分（正数表示目标高于当前）`,
        confidenceLabel: "较高",
        confidenceTone: "high",
        reliableJudgment: `行测、申论与主目标岗位已经采用同一有效口径，可以计算当前总分区间和目标区间。${base.goalLabel} 已作为本次计划依据。`,
        riskJudgment:
          "当前能力仍会随新模考、正式成绩和稳定练习表现更新；这是一份动态区间判断，不是上岸保证。",
        primarySuggestionTitle: "开始今日学习",
        primarySuggestionCopy: "按当前分差和可用时间执行第一组任务",
      };
    }
    case "baseline_required":
      return {
        ...base,
        eyebrow: "双科成绩记录还不完整",
        title: baselineTitle(result),
        description:
          "先补充同一口径下的行测与申论真实成绩。单科分、旧档案分和单篇 AI 训练估分都不会被拿来凑总分。",
        actionLabel: baselineActionLabel(result.nextAction),
        confidenceLabel: "待校准",
        confidenceTone: "calibrating",
        reliableJudgment:
          "报考资格与学习时间已经确认，可以安排基础任务；现有成绩证据还不足以形成行测、申论共同总分区间。",
        riskJudgment:
          "当前不能计算可信岗位分差。自报成绩只作为初始锚点，完成系统摸底或导入有效成绩后会重新校准。",
        primarySuggestionTitle: baselineActionLabel(result.nextAction),
        primarySuggestionCopy: baselineSuggestionCopy(result.nextAction),
      };
    case "position_selection_required":
      return {
        ...base,
        eyebrow: "资格范围已生成 · 岗位可以稍后再选",
        title: "先开始学习，目标岗位可以边练边定",
        description: "当前资格范围已经保存。你可以直接开始第一组训练，确认主目标后再补充岗位分差。",
        actionLabel: "先进入今日学习",
        confidenceLabel: "学习可开始",
        confidenceTone: "calibrating",
        reliableJudgment:
          "当前资格事实已经形成候选范围，现有资料足以安排起点训练；系统不会替你擅自决定主报岗位。",
        riskJudgment: "在你确认主目标前只是不计算岗位分差，不影响行测、申论诊断和日常训练。",
        primarySuggestionTitle: "开始第一组任务",
        primarySuggestionCopy: "先建立真实能力起点，岗位可以稍后再完善",
      };
    case "no_eligible_position":
      return {
        ...base,
        eyebrow: "岗位条件仍有待确认项",
        title: "先开始学习，岗位核验不会卡住你",
        description:
          "系统会保留岗位原文和资格事实供你稍后核验；当前先按国考共同能力要求建立训练起点。",
        actionLabel: "先进入今日学习",
        confidenceLabel: "岗位待核验",
        confidenceTone: "review",
        reliableJudgment:
          "资格档案已保存，当前岗位规则没有形成可直接确认的结论；这不会阻止行测和申论训练。",
        riskJudgment:
          "这不等于最终无岗。职位变化、证明材料补充或资格事实修订后，候选范围仍会重新计算。",
        primarySuggestionTitle: "开始第一组任务",
        primarySuggestionCopy: "先建立真实能力起点，之后再核验岗位条件",
      };
  }
}

function baselineSuggestionCopy(action: OnboardingResultAction): string {
  return {
    enter_workspace: "按现有资格与时间先开始第一组任务",
    record_baseline: "完成一套约 30 分钟的双科起点诊断",
    choose_position: "选择主目标后再建立岗位分差",
    review_exam_briefing: "先理解本次国考口径与岗位证据",
    import_prior_evidence: "把已有成绩转成可追溯的能力证据",
    start_retake_recovery: "从上一次考试的失分结构重新起步",
  }[action];
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
  return value ? `${numberLabel(value.lower)}–${numberLabel(value.upper)}` : "数据不足";
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
  if (Number.isNaN(date.getTime())) return "记录时间";
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
