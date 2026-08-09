import type { DailyCapacityCommand, TodayCapacity } from "../../api/generated/types.gen";
import { createIdempotencyKey, formatIdempotencyKey } from "../../platform/command-id";

export interface CapacityDraft {
  availableMinutes: string;
  energy: number;
  stress: number;
  confidenceLevel: number;
  preferredXingceModule: DailyCapacityCommand["preferredXingceModule"];
  xingceOnly: boolean;
  preferredTrack: DailyCapacityCommand["preferredTrack"];
}

export interface CapacityTimeOption {
  minutes: number;
  value: string;
  label: string;
  caption: string;
}

export interface PreparedCapacityCommand {
  command: DailyCapacityCommand | null;
  error: string;
}

export interface CapacityWritePolicy {
  allowed: boolean;
  notice: string;
}

type CapacityMetric = "energy" | "stress" | "confidence";

const scaleLabels: Readonly<Record<CapacityMetric, readonly string[]>> = {
  energy: ["极低", "偏低", "一般", "不错", "充沛"],
  stress: ["放松", "较低", "一般", "较高", "很高"],
  confidence: ["很不确定", "把握较低", "一般", "把握较高", "很确定"],
};

export const capacityTimeOptions: ReadonlyArray<CapacityTimeOption> = Array.from(
  { length: 25 },
  (_, index) => index * 30,
).map((minutes) => ({
  minutes,
  value: String(minutes),
  label: formatCapacityDuration(minutes),
  caption: capacityDurationCaption(minutes),
}));

export function capacityDraftFromVersion(
  capacity: Pick<
    TodayCapacity,
    | "availableMinutes"
    | "energy"
    | "stress"
    | "confidenceLevel"
    | "preferredXingceModule"
    | "xingceOnly"
    | "preferredTrack"
  >,
): CapacityDraft {
  return {
    availableMinutes: String(capacity.availableMinutes),
    energy: capacity.energy,
    stress: capacity.stress,
    confidenceLevel: capacity.confidenceLevel,
    preferredXingceModule: capacity.preferredXingceModule ?? null,
    xingceOnly: capacity.xingceOnly ?? false,
    preferredTrack: capacity.preferredTrack ?? "balanced",
  };
}

export function prepareCapacityCommand(
  draft: CapacityDraft,
  basedOnCapacityVersionId: string,
): PreparedCapacityCommand {
  const minutesText = draft.availableMinutes.trim();
  if (!/^\d{1,4}$/.test(minutesText)) {
    return { command: null, error: "请输入 0–1440 之间的分钟数。" };
  }
  const availableMinutes = Number(minutesText);
  if (!Number.isInteger(availableMinutes) || availableMinutes < 0 || availableMinutes > 1440) {
    return { command: null, error: "今日可用时间要在 0–1440 分钟之间哦。" };
  }
  if (!basedOnCapacityVersionId.trim()) {
    return { command: null, error: "今天的计划更新过啦，重新生成一下再保存就好。" };
  }
  for (const [label, value] of [
    ["精力", draft.energy],
    ["压力", draft.stress],
    ["时间把握", draft.confidenceLevel],
  ] as const) {
    if (!Number.isInteger(value) || value < 1 || value > 5) {
      return { command: null, error: `${label}要选 1–5 级哦。` };
    }
  }

  return {
    command: {
      basedOnCapacityVersionId,
      availableMinutes,
      energy: draft.energy,
      stress: draft.stress,
      confidenceLevel: draft.confidenceLevel,
      preferredTrack: draft.preferredTrack ?? "balanced",
      preferredXingceModule: draft.preferredXingceModule ?? null,
      xingceOnly: Boolean(draft.preferredXingceModule && draft.xingceOnly),
    },
    error: "",
  };
}

export function capacityCommandFingerprint(command: DailyCapacityCommand): string {
  return [
    command.basedOnCapacityVersionId,
    command.availableMinutes,
    command.energy,
    command.stress,
    command.confidenceLevel,
    command.preferredTrack ?? "balanced",
    command.preferredXingceModule ?? "balanced",
    command.xingceOnly ? "only" : "mixed",
  ].join(":");
}

export function capacityScaleLabel(metric: CapacityMetric, value: number): string {
  return scaleLabels[metric][value - 1] ?? "未选择";
}

export function capacityDurationLabel(draft: Pick<CapacityDraft, "availableMinutes">): string {
  const minutes = Number(draft.availableMinutes);
  return Number.isInteger(minutes) ? formatCapacityDuration(minutes) : "时间待确认";
}

export function formatCapacityDuration(minutes: number): string {
  if (!Number.isInteger(minutes) || minutes < 0 || minutes > 1440) return "时间待确认";
  if (minutes === 0) return "今天不安排";
  if (minutes % 60 === 0) return `${String(minutes / 60)} 小时`;
  if (minutes % 30 === 0) return `${trimHalfHour(minutes / 60)} 小时`;
  return `${String(minutes)} 分钟`;
}

export function capacityWritePolicy(accessToken?: string): CapacityWritePolicy {
  if (!accessToken) {
    return {
      allowed: false,
      notice: "登录状态已失效，重新登录后才能确认今日容量。",
    };
  }
  return { allowed: true, notice: "保存后，岸岸会按新的可用时间重新安排今天。" };
}

export function formatCapacityIdempotencyKey(randomValues: ArrayBuffer): string {
  return formatIdempotencyKey("capacity", randomValues);
}

export async function createCapacityIdempotencyKey(): Promise<string> {
  return createIdempotencyKey("capacity");
}

function trimHalfHour(hours: number): string {
  return hours.toFixed(1).replace(/\.0$/u, "");
}

function capacityDurationCaption(minutes: number): string {
  if (minutes === 0) return "完全休息";
  if (minutes <= 60) return "碎片日";
  if (minutes <= 180) return "在职友好";
  if (minutes <= 360) return "集中训练";
  if (minutes <= 600) return "全职高强度";
  return "卷王模式";
}
