import type {
  AbilityEstimateCurrent,
  AbilityEstimateVersion,
} from "../../../../api/generated/types.gen";

export interface AbilityEstimateView {
  readiness: AbilityReadiness;
  statusLabel: string;
  totalLabel: string;
  xingceLabel: string;
  shenlunLabel: string;
  confidenceLabel: string;
  evidenceLabel: string;
  missingLabel: string;
  gapBlockDetail: string;
  conflictLabel: string;
  canCalculatePositionGap: boolean;
}

export type AbilityReadiness = AbilityEstimateCurrent["readiness"] | "stale";

export type AbilityEstimateCurrentViewSource = Omit<AbilityEstimateCurrent, "readiness"> & {
  readiness: AbilityReadiness;
};

type AbilityScoreRange = NonNullable<AbilityEstimateVersion["xingce"]>;

export function abilityEstimateView(
  current: AbilityEstimateCurrentViewSource,
): AbilityEstimateView {
  const estimate = current.estimate;
  const summary = estimate?.evidenceSummary;
  const missingLabels = current.missingComponents.map(componentLabel);
  const conflicts = summary?.conflictComponents.map(componentLabel) ?? [];
  return {
    readiness: current.readiness,
    statusLabel: {
      unavailable: "等待双科成绩",
      partial: "双科成绩还不完整",
      complete: "综合能力区间已建立",
      stale: "总分记录已过期",
    }[current.readiness],
    totalLabel: rangeLabel(estimate?.total),
    xingceLabel: rangeLabel(estimate?.xingce),
    shenlunLabel: rangeLabel(estimate?.shenlun),
    confidenceLabel: confidenceLabel(estimate),
    evidenceLabel: summary
      ? `${String(summary.evidenceCount)} 条已确认成绩记录`
      : "尚无经本人确认的成绩记录",
    missingLabel:
      current.readiness === "stale"
        ? "需补充新成绩"
        : missingLabels.length
          ? `还需补充${missingLabels.join("和")}`
          : "行测、申论口径完整",
    gapBlockDetail:
      current.readiness === "stale"
        ? "过期记录不会继续参与岗位分差。"
        : "补齐前不会拿单科分冒充总分。",
    conflictLabel: conflicts.length
      ? `${conflicts.join("、")}成绩记录存在明显冲突，区间已扩大`
      : "",
    canCalculatePositionGap:
      current.readiness === "complete" && estimate?.total !== null && estimate?.total !== undefined,
  };
}

function rangeLabel(scoreRange: AbilityScoreRange | null | undefined): string {
  if (!scoreRange) return "待补证据";
  const trim = (value: number): string =>
    Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${trim(scoreRange.lower)}–${trim(scoreRange.upper)}`;
}

function componentLabel(component: "xingce" | "shenlun"): string {
  return component === "xingce" ? "行测" : "申论";
}

function confidenceLabel(estimate: AbilityEstimateVersion | null | undefined): string {
  if (!estimate) return "暂不可用";
  return {
    low: "低置信",
    medium: "中置信",
    high: "高置信",
  }[estimate.confidence];
}
