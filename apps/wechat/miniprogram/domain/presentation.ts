import type { EvidenceLevel, TodayTask } from "./contracts";

export function evidenceLabel(level: EvidenceLevel): string {
  return {
    unknown: "证据状态未知",
    low: "证据较少",
    medium: "持续校准",
    high: "高可信",
  }[level];
}

export function scoreRangeLabel(lower: number, upper: number): string {
  return `${lower.toFixed(0)}–${upper.toFixed(0)} 分`;
}

export function orderedTasks(tasks: TodayTask[]): TodayTask[] {
  return [...tasks].sort((left, right) => left.priority - right.priority);
}

export function percentLabel(value: number): string {
  return `${String(Math.round(value * 100))}%`;
}
