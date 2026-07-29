import type {
  PositionComparisonHighlights,
  PositionComparisonItem,
  PositionDiscoveryItem,
  PositionFitAssessment,
  PositionWorkspaceItem,
  PrimaryGoalPreview,
  QualificationGap,
  RequirementCheck,
} from "../../api/generated/types.gen";
import { targetValidityView } from "./contracts";
import type { PositionRouteSummary } from "./contracts";

export interface PositionCardView extends PositionDiscoveryItem {
  eligibilityLabel: string;
  eligibilityTone: "success" | "warning" | "muted";
  scoreLabel: string;
  scoreEvidenceLabel: string;
  targetValidityLabel: string;
  targetGeneratedLabel: string;
  targetValidityAvailable: boolean;
  targetScoreExpired: boolean;
  conditionSummary: string;
}

export interface RequirementCheckView extends RequirementCheck {
  statusLabel: string;
  statusTone: "success" | "warning" | "danger";
  fieldLabel: string;
  conditionLabel: string;
  evidenceLabel: string;
}

export interface PrimaryGoalPreviewView {
  previousScoreLabel: string;
  nextScoreLabel: string;
  deltaLabel: string;
  expiryLabel: string;
}

export interface PositionComparisonItemView extends PositionCardView {
  paperTypeLabel: string;
  highlightLabels: string[];
}

export interface PositionFitView extends PositionFitAssessment {
  statusLabel: string;
  statusDescription: string;
  statusTone: "success" | "warning" | "danger" | "muted";
  bandLabel: string;
  evidenceStatusLabel: string;
  evidenceReasonLabel: string;
  scoreGapRangeLabel: string;
  scoreGapRecommendedLabel: string;
  scoreGapDirectionLabel: string;
  requirementsBurdenLabel: string;
  additionalExamLabel: string;
  pendingRequirementIdsLabel: string;
  hardConditionNotMetRequirementIdsLabel: string;
  scoreGapAvailable: boolean;
  nextActionTitle: string;
  nextActionCopy: string;
}

export interface PositionWorkspaceItemView extends PositionCardView {
  fit: PositionFitAssessment;
  fitView: PositionFitView;
}

export interface QualificationGapView extends QualificationGap {
  gapKey: string;
  factLabel: string;
  statusLabel: string;
  actionLabel: string;
  tone: "warning" | "danger";
}

export interface QualificationActionView {
  title: string;
  copy: string;
  label: string;
}

export interface PositionRouteSummaryView extends PositionRouteSummary {
  eligibilityLabel: string;
  eligibilityTone: PositionCardView["eligibilityTone"];
  scoreLabel: string;
  scoreEvidenceLabel: string;
  targetValidityLabel: string;
  targetGeneratedLabel: string;
  targetValidityAvailable: boolean;
  targetScoreExpired: boolean;
}

export function positionCardView(item: PositionDiscoveryItem): PositionCardView {
  const status = eligibilityPresentation(item.eligibilityStatus);
  const target = item.targetScore;
  const validity = targetValidityView(target);
  return {
    ...item,
    eligibilityLabel: status[0],
    eligibilityTone: status[1],
    scoreLabel: target
      ? `${target.lowerBound.toFixed(1)}–${target.upperBound.toFixed(1)}`
      : "暂无可靠区间",
    scoreEvidenceLabel: target
      ? `${targetBasisLabel(target.basisLevel)} · ${targetEvidenceLabel(target.evidenceLevel)} · ${String(target.sampleCount)} 个真实样本`
      : "需要更多进面分证据",
    targetValidityLabel: validity.label,
    targetGeneratedLabel: validity.generatedLabel,
    targetValidityAvailable: validity.available,
    targetScoreExpired: validity.expired,
    conditionSummary: conditionSummary(item),
  };
}

export function positionRouteSummaryView(item: PositionRouteSummary): PositionRouteSummaryView {
  const status = eligibilityPresentation(item.eligibilityStatus);
  const target = item.targetScore;
  const validity = targetValidityView(target);
  return {
    ...item,
    eligibilityLabel: status[0],
    eligibilityTone: status[1],
    scoreLabel: target
      ? `${target.lowerBound.toFixed(1)}–${target.upperBound.toFixed(1)}`
      : "暂无可靠区间",
    scoreEvidenceLabel: target
      ? `${targetBasisLabel(target.basisLevel)} · ${targetEvidenceLabel(target.evidenceLevel)} · ${String(target.sampleCount)} 个真实样本`
      : "需要更多进面分证据",
    targetValidityLabel: validity.label,
    targetGeneratedLabel: validity.generatedLabel,
    targetValidityAvailable: validity.available,
    targetScoreExpired: validity.expired,
  };
}

export function requirementCheckView(check: RequirementCheck): RequirementCheckView {
  const evidenceIds = check.evidenceIds ?? [];
  const fieldLabels: Record<string, string> = {
    education: "学历",
    degree: "学位",
    major: "专业",
    graduation_year: "毕业年份",
    fresh_graduate: "应届身份",
    political_status: "政治面貌",
    work_years: "普通工作经历",
    grassroots_years: "基层工作经历",
    service_project: "服务基层项目",
    hukou: "户籍 / 生源地",
    age: "年龄",
    certifications: "资格证书",
    other_conditions: "其他报考条件",
  };
  const status = {
    met: ["符合", "success"],
    needs_confirmation: ["待确认", "warning"],
    not_met: ["不符合", "danger"],
  }[check.status] as [string, RequirementCheckView["statusTone"]];
  return {
    ...check,
    statusLabel: status[0],
    statusTone: status[1],
    fieldLabel: fieldLabels[check.field ?? ""] ?? "报考条件",
    conditionLabel: check.sourceText ?? "岗位结构化条件尚未完成",
    evidenceLabel: check.confirmationId
      ? "引用本人针对该岗位原文的明确确认"
      : evidenceIds.length > 0
        ? `引用 ${String(evidenceIds.length)} 条本人资格事实证据`
        : check.reasonCode === "requirements_missing"
          ? "该岗位的报考条件还未整理完整"
          : check.reasonCode === "source_conditions_incomplete"
            ? "当前岗位版本缺少完整官方备注，不能确认可报"
            : "需要补充或确认资格事实",
  };
}

export function primaryGoalPreviewView(preview: PrimaryGoalPreview): PrimaryGoalPreviewView {
  const previousTargetScore = preview.planImpact.previousTargetScore ?? null;
  const delta = preview.planImpact.targetScoreDelta ?? null;
  const expiry = new Date(preview.expiresAt);
  return {
    previousScoreLabel: previousTargetScore === null ? "尚未设置" : previousTargetScore.toFixed(1),
    nextScoreLabel: preview.planImpact.nextTargetScore.toFixed(1),
    deltaLabel:
      delta === null
        ? "首次建立主目标"
        : delta === 0
          ? "目标分不变"
          : `${delta > 0 ? "提高" : "降低"} ${Math.abs(delta).toFixed(1)} 分`,
    expiryLabel: `${String(expiry.getHours()).padStart(2, "0")}:${String(
      expiry.getMinutes(),
    ).padStart(2, "0")} 前确认有效`,
  };
}

export function positionComparisonItemView(
  item: PositionComparisonItem,
  highlights: PositionComparisonHighlights,
): PositionComparisonItemView {
  const base = positionCardView(item);
  const highlightLabels: string[] = [];
  if (highlights.confirmedEligiblePositionIds.includes(item.positionId)) {
    highlightLabels.push("资格已确认");
  }
  if (highlights.largestHeadcountPositionIds.includes(item.positionId)) {
    highlightLabels.push("招录人数最多");
  }
  if (highlights.lowestTargetScorePositionIds.includes(item.positionId)) {
    highlightLabels.push("可靠目标分最低");
  }
  if (highlights.primaryGoalPositionId === item.positionId) {
    highlightLabels.push("当前主目标");
  }
  const paperTypeLabels: Record<string, string> = {
    administrativeLaw: "行政执法类",
    subProvincial: "副省级",
    prefecture: "地市级",
  };
  return {
    ...base,
    paperTypeLabel: item.paperType
      ? (paperTypeLabels[item.paperType] ?? item.paperType)
      : "试卷类型待确认",
    highlightLabels,
  };
}

export function positionWorkspaceItemView(item: PositionWorkspaceItem): PositionWorkspaceItemView {
  return {
    ...positionCardView(item),
    fit: item.fit,
    fitView: positionFitView(item.fit),
  };
}

export function positionFitView(fit: PositionFitAssessment): PositionFitView {
  const relation = {
    conservative: {
      label: "稳妥区间",
      description: "当前能力区间高于岗位目标区间",
      tone: "success",
      band: "稳妥",
    },
    overlap: {
      label: "匹配区间",
      description: "当前能力区间与岗位目标区间重叠",
      tone: "warning",
      band: "匹配",
    },
    below_target: {
      label: "冲刺区间",
      description: "岗位目标区间高于当前能力区间",
      tone: "danger",
      band: "冲刺",
    },
    evidence_unavailable: {
      label: "待补成绩",
      description: "资格已先核验；录入行测和申论成绩后再计算分差",
      tone: "muted",
      band: "待算分差",
    },
  }[fit.status] as {
    label: string;
    description: string;
    tone: PositionFitView["statusTone"];
    band: string;
  };
  const evidenceStatusLabel = {
    unavailable: "待录入双科成绩",
    partial: "还差一科成绩",
    stale: "成绩记录已过期",
    ready: "同年度双科证据完整",
  }[fit.evidenceStatus];
  const reasonLabels: Record<PositionFitAssessment["evidenceReasonCodes"][number], string> = {
    ability_estimate_unavailable: "还没有录入行测和申论成绩",
    ability_estimate_partial: "行测与申论成绩未补齐",
    ability_estimate_stale: "成绩记录已过期",
    ability_estimate_exam_year_mismatch: "成绩证据与岗位年度不一致",
    target_score_unavailable: "岗位进面分样本不足",
  };
  const burdenParts: string[] = [];
  if (fit.requirementsBurden.additionalExamRequired) burdenParts.push("需要额外考试");
  if (fit.requirementsBurden.pendingRequirementCount > 0) {
    burdenParts.push(`${String(fit.requirementsBurden.pendingRequirementCount)} 项资格待确认`);
  }
  if (fit.requirementsBurden.hardConditionNotMetCount > 0) {
    burdenParts.push(`${String(fit.requirementsBurden.hardConditionNotMetCount)} 项硬条件不符合`);
  }
  const gap = fit.scoreGap;
  const nextAction = positionFitNextAction(fit, relation.label);
  return {
    ...fit,
    statusLabel: relation.label,
    statusDescription: relation.description,
    statusTone: relation.tone,
    bandLabel: fit.band === null ? "未分带" : relation.band,
    evidenceStatusLabel,
    evidenceReasonLabel: fit.evidenceReasonCodes.map((code) => reasonLabels[code]).join("；"),
    scoreGapRangeLabel: gap
      ? `${signedScore(gap.lower)} 至 ${signedScore(gap.upper)} 分`
      : "录入双科成绩后计算",
    scoreGapRecommendedLabel: gap ? `建议点差 ${signedScore(gap.recommended)} 分` : "分差暂不可算",
    scoreGapDirectionLabel: gap ? "正数表示岗位目标高于当前能力" : "",
    requirementsBurdenLabel: burdenParts.length
      ? burdenParts.join("；")
      : "未发现额外考试或待处理硬条件",
    additionalExamLabel: fit.requirementsBurden.additionalExamRequired
      ? "需要额外考试"
      : "无额外考试要求",
    pendingRequirementIdsLabel: fit.requirementsBurden.pendingRequirementIds.join("、"),
    hardConditionNotMetRequirementIdsLabel:
      fit.requirementsBurden.hardConditionNotMetRequirementIds.join("、"),
    scoreGapAvailable: gap !== null,
    nextActionTitle: nextAction.title,
    nextActionCopy: nextAction.copy,
  };
}

export function qualificationGapView(gap: QualificationGap): QualificationGapView {
  const factLabels: Record<QualificationGap["factKey"], string> = {
    education: "学历",
    degree: "学位",
    major: "专业",
    graduation_year: "毕业年份",
    fresh_graduate: "应届身份",
    political_status: "政治面貌",
    work_years: "普通工作经历",
    grassroots_years: "基层经历",
    service_project: "服务项目经历",
    hukou: "户籍 / 生源地",
    age: "年龄",
    certifications: "资格证书",
    other_conditions: "岗位备注条件",
  };
  const isPositionConfirmation = gap.gapType === "position_confirmation";
  return {
    ...gap,
    gapKey: `${gap.factKey}:${gap.gapType}`,
    factLabel: factLabels[gap.factKey],
    statusLabel:
      gap.gapType === "conflicting_fact"
        ? "事实存在冲突"
        : isPositionConfirmation
          ? "需按岗位原文确认"
          : "档案尚未填写",
    actionLabel: isPositionConfirmation ? "在待确认岗位中处理" : "补充资格档案",
    tone: gap.gapType === "conflicting_fact" ? "danger" : "warning",
  };
}

export function qualificationActionView(
  gaps: QualificationGapView[],
): QualificationActionView | null {
  if (gaps.length === 0) return null;
  const profileGaps = gaps.filter((gap) => gap.action === "complete_profile");
  if (profileGaps.length === 0) return null;
  return {
    title: `还有 ${String(profileGaps.length)} 项报考资料待补充`,
    copy: "补齐档案后，岗位判断会自动更新。",
    label: "去补充",
  };
}

function conditionSummary(item: PositionDiscoveryItem): string {
  if (item.reasonCodes.includes("source_conditions_incomplete")) {
    return "完整官方条件尚未入库，暂不判断可报";
  }
  if (item.eligibilityStatus === "eligible") {
    return `${String(item.metCount)} 项硬条件已逐条匹配`;
  }
  if (item.eligibilityStatus === "needs_confirmation") {
    return `${String(item.pendingCount)} 项条件需要你确认`;
  }
  return `${String(item.notMetCount)} 项硬条件不符合`;
}

function eligibilityPresentation(
  status: PositionDiscoveryItem["eligibilityStatus"],
): [string, PositionCardView["eligibilityTone"]] {
  return {
    eligible: ["可报", "success"],
    needs_confirmation: ["待确认", "warning"],
    ineligible: ["不可报", "muted"],
  }[status] as [string, PositionCardView["eligibilityTone"]];
}

function targetBasisLabel(
  basisLevel: NonNullable<PositionDiscoveryItem["targetScore"]>["basisLevel"],
): string {
  return {
    direct: "岗位当前发布直接依据",
    L1: "该岗位近年样本",
    L2: "相似地区与条件岗位",
    L3: "同地市同系统岗位",
    L4: "同省同系统岗位",
  }[basisLevel];
}

function targetEvidenceLabel(
  evidenceLevel: NonNullable<PositionDiscoveryItem["targetScore"]>["evidenceLevel"],
): string {
  return {
    direct: "直接证据",
    strong: "强证据",
    limited: "有限证据",
    unavailable: "证据不可用",
  }[evidenceLevel];
}

function signedScore(value: number): string {
  if (value > 0) return `+${value.toFixed(1)}`;
  return value.toFixed(1);
}

function positionFitNextAction(
  fit: PositionFitAssessment,
  statusLabel: string,
): { title: string; copy: string } {
  if (fit.requirementsBurden.hardConditionNotMetCount > 0) {
    return {
      title: "先放弃或复核硬条件",
      copy: "当前岗位存在明确不符合项；除非你能拿出新事实，否则不建议设为主目标。",
    };
  }
  if (fit.requirementsBurden.pendingRequirementCount > 0) {
    return {
      title: "先确认资格原文",
      copy: "这类岗位还缺本人确认事实；处理完待确认条件后，可报结论才算稳定。",
    };
  }
  if (fit.evidenceReasonCodes.includes("target_score_unavailable")) {
    return {
      title: "目标分依据不足",
      copy: "可以先收藏或查看依据，但系统不会用样本不足的岗位线硬算分差。",
    };
  }
  if (fit.evidenceStatus === "unavailable" || fit.evidenceStatus === "partial") {
    return {
      title: "补齐行测 + 申论成绩",
      copy: "资格已经先筛过；录入同一口径双科成绩后，才会生成可信分差和今日计划影响。",
    };
  }
  if (fit.evidenceStatus === "stale") {
    return {
      title: "更新近期成绩证据",
      copy: "旧成绩不会继续参与岗位分差；补一条近期双科成绩后再比较。",
    };
  }
  if (fit.status === "evidence_unavailable") {
    return {
      title: "证据还没凑齐",
      copy: "当前只保留资格筛选结果，不会把缺失证据包装成推荐。",
    };
  }
  return {
    title: `当前属于${statusLabel}`,
    copy: "可点进岗位查看资格依据、分数依据和设为主目标后的计划影响。",
  };
}
