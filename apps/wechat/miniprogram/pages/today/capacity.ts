import type { DailyCapacityCommand, TodayCapacity } from "../../api/generated/types.gen";
import type { DataMode } from "../../config/environment";
import { createIdempotencyKey, formatIdempotencyKey } from "../../platform/command-id";

export interface CapacityDraft {
  availableMinutes: string;
  energy: number;
  stress: number;
  confidenceLevel: number;
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

export function capacityDraftFromVersion(
  capacity: Pick<TodayCapacity, "availableMinutes" | "energy" | "stress" | "confidenceLevel">,
): CapacityDraft {
  return {
    availableMinutes: String(capacity.availableMinutes),
    energy: capacity.energy,
    stress: capacity.stress,
    confidenceLevel: capacity.confidenceLevel,
  };
}

export function prepareCapacityCommand(
  draft: CapacityDraft,
  basedOnCapacityVersionId: string,
): PreparedCapacityCommand {
  const minutesText = draft.availableMinutes.trim();
  if (!/^\d{1,4}$/.test(minutesText)) {
    return { command: null, error: "请输入 0–1440 之间的完整分钟数。" };
  }
  const availableMinutes = Number(minutesText);
  if (!Number.isInteger(availableMinutes) || availableMinutes < 0 || availableMinutes > 1440) {
    return { command: null, error: "今日可用时间必须在 0–1440 分钟之间。" };
  }
  if (!basedOnCapacityVersionId.trim()) {
    return { command: null, error: "当日容量版本缺失，请重新载入今日计划。" };
  }
  for (const [label, value] of [
    ["精力", draft.energy],
    ["压力", draft.stress],
    ["时间把握", draft.confidenceLevel],
  ] as const) {
    if (!Number.isInteger(value) || value < 1 || value > 5) {
      return { command: null, error: `${label}必须选择 1–5 级。` };
    }
  }

  return {
    command: {
      basedOnCapacityVersionId,
      availableMinutes,
      energy: draft.energy,
      stress: draft.stress,
      confidenceLevel: draft.confidenceLevel,
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
  ].join(":");
}

export function capacityScaleLabel(metric: CapacityMetric, value: number): string {
  return scaleLabels[metric][value - 1] ?? "未选择";
}

export function capacityWritePolicy(dataMode: DataMode, accessToken?: string): CapacityWritePolicy {
  if (dataMode === "prototype") {
    return {
      allowed: false,
      notice: "当前是交互稿数据，可以体验编辑，但不会伪造容量版本或计划重排。",
    };
  }
  if (!accessToken) {
    return {
      allowed: false,
      notice: "登录状态已失效，重新登录后才能确认今日容量。",
    };
  }
  return { allowed: true, notice: "保存后，规划 Agent 会按新容量重排完整的今日计划。" };
}

export function formatCapacityIdempotencyKey(randomValues: ArrayBuffer): string {
  return formatIdempotencyKey("capacity", randomValues);
}

export async function createCapacityIdempotencyKey(): Promise<string> {
  return createIdempotencyKey("capacity");
}
