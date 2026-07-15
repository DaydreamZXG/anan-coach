import type {
  PositionDiscoveryItem,
  PrimaryGoalPlanImpact,
  TargetScoreRange,
} from "../../api/generated/types.gen";
import type { ApiEnvelope } from "../../domain/contracts";

export type PositionRemoteStatus =
  | "idle"
  | "loading"
  | "ready"
  | "empty"
  | "needsProfile"
  | "noActiveRelease"
  | "versionConflict"
  | "notFound"
  | "network"
  | "error";

export interface PositionRequestTrace {
  requestId: string;
  generatedAt: string;
}

export interface PositionOperationError {
  status: Exclude<PositionRemoteStatus, "idle" | "loading" | "ready" | "empty">;
  title: string;
  message: string;
  requestId: string;
  retryable: boolean;
}

export type PositionCapability = "decision_buckets" | "preparation_cost" | "score_gap";

export interface PositionCapabilityGap {
  capability: PositionCapability;
  title: string;
  description: string;
}

export interface PositionDeliveryView extends PositionRequestTrace {
  partial: boolean;
  stale: boolean;
  processing: boolean;
  partialLabel: string;
  staleLabel: string;
  processingLabel: string;
  generatedAtLabel: string;
}

export interface PositionRouteSummary {
  positionId: string;
  positionCode: string;
  name: string;
  departmentFull: string;
  region: string;
  city: string | null;
  headcount: number;
  eligibilityStatus: PositionDiscoveryItem["eligibilityStatus"];
  targetScore: TargetScoreRange | null;
  isPrimaryGoal: boolean;
}

export interface PositionTargetValidityView {
  available: boolean;
  label: string;
  expired: boolean;
  generatedLabel: string;
}

export interface PlanInvalidationView {
  code: PrimaryGoalPlanImpact["invalidates"][number];
  label: string;
}

export function requestTrace<T>(envelope: ApiEnvelope<T>): PositionRequestTrace {
  return {
    requestId: envelope.requestId,
    generatedAt: envelope.generatedAt,
  };
}

export function deliveryView<T>(envelope: ApiEnvelope<T>): PositionDeliveryView {
  return {
    ...requestTrace(envelope),
    partial: false,
    stale: false,
    processing: false,
    partialLabel: "",
    staleLabel: "",
    processingLabel: "",
    generatedAtLabel: formatTimestamp(envelope.generatedAt),
  };
}

export function targetValidityView(
  targetScore: TargetScoreRange | null | undefined,
  now = Date.now(),
): PositionTargetValidityView {
  if (!targetScore) {
    return { available: false, label: "暂无目标分证据", expired: false, generatedLabel: "" };
  }
  const validUntil = targetScore.validUntil;
  const timestamp = new Date(validUntil).getTime();
  if (!Number.isFinite(timestamp)) {
    return {
      available: false,
      label: "目标分有效期格式无效",
      expired: false,
      generatedLabel: `目标分生成 ${formatTimestamp(targetScore.generatedAt)}`,
    };
  }
  const expired = timestamp <= now;
  return {
    available: true,
    expired,
    label: `${expired ? "已于" : "有效至"} ${formatTimestamp(validUntil)}`,
    generatedLabel: `目标分生成 ${formatTimestamp(targetScore.generatedAt)}`,
  };
}

export function planInvalidationViews(
  invalidates: PrimaryGoalPlanImpact["invalidates"],
): PlanInvalidationView[] {
  const labels: Record<PrimaryGoalPlanImpact["invalidates"][number], string> = {
    daily_plan: "当日计划",
    weekly_plan: "本周计划",
    score_gap: "岗位分差",
  };
  return invalidates.map((code) => ({ code, label: `${labels[code]}将由服务端重建` }));
}

export function positionRouteSummary(item: PositionDiscoveryItem): PositionRouteSummary {
  return {
    positionId: item.positionId,
    positionCode: item.positionCode,
    name: item.name,
    departmentFull: item.departmentFull,
    region: item.region,
    city: item.city ?? null,
    headcount: item.headcount,
    eligibilityStatus: item.eligibilityStatus,
    targetScore: item.targetScore ?? null,
    isPrimaryGoal: item.isPrimaryGoal,
  };
}

export const positionCapabilityGaps: Record<PositionCapability, PositionCapabilityGap> = {
  decision_buckets: {
    capability: "decision_buckets",
    title: "冲稳保组合尚无正式服务端结论",
    description: "当前只展示资格与目标分证据，不在客户端推算冲、稳、保。",
  },
  preparation_cost: {
    capability: "preparation_cost",
    title: "备考成本尚无正式服务端口径",
    description: "当前接口没有可审计的学习成本分解，本页不会用招录人数或分数区间代替。",
  },
  score_gap: {
    capability: "score_gap",
    title: "岗位分差尚无同版本服务端输出",
    description: "不会在客户端将能力区间与岗位区间自行相减。",
  },
};

function formatTimestamp(value: string): string {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "时间未知";
  const date = new Date(timestamp);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}
