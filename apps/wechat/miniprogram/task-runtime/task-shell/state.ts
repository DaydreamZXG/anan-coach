export type TaskShellSyncTone = "saved" | "saving" | "pending" | "error";

const ERROR_LABELS = new Set(["保存失败", "同步失败", "交卷结果待确认"]);
const SAVING_LABELS = new Set(["正在保存", "正在写入版本", "正在交卷"]);
const PENDING_LABELS = new Set([
  "连接服务端",
  "待保存",
  "等待你的确认",
  "尚未保存本次修改",
  "上次断点待确认",
]);

export function taskShellSyncTone(label: string): TaskShellSyncTone {
  const normalized = label.trim();
  if (ERROR_LABELS.has(normalized)) return "error";
  if (SAVING_LABELS.has(normalized)) return "saving";
  if (PENDING_LABELS.has(normalized) || normalized.includes("待确认")) return "pending";
  if (
    normalized.startsWith("已") ||
    normalized.includes("已保存") ||
    normalized.includes("已结算")
  ) {
    return "saved";
  }
  return "pending";
}
