import type { AppDomainsPlanningSchemasTodayTask } from "../../../../api/generated/types.gen";
import { durationLabel } from "./presentation";

export type CapacityChangeKind = "retained" | "shortened" | "extended" | "added" | "removed";
export type CapacityChangeTone = "blue" | "cyan" | "indigo" | "green" | "orange";

export type CapacityComparableTask = Pick<
  AppDomainsPlanningSchemasTodayTask,
  "estimatedMinutes" | "kind" | "reason" | "stage" | "title" | "track"
>;

export interface CapacityChangeView {
  id: string;
  kind: CapacityChangeKind;
  tone: CapacityChangeTone;
  label: string;
  title: string;
  duration: string;
  description: string;
}

const changePresentation: Record<CapacityChangeKind, Pick<CapacityChangeView, "label" | "tone">> = {
  retained: { label: "保留", tone: "blue" },
  shortened: { label: "缩短", tone: "cyan" },
  extended: { label: "延长", tone: "indigo" },
  added: { label: "新增", tone: "green" },
  removed: { label: "今天不排", tone: "orange" },
};

export function presentCapacityChanges(
  currentTasks: readonly CapacityComparableTask[],
  proposedTasks: readonly CapacityComparableTask[],
): CapacityChangeView[] {
  const currentBuckets = new Map<string, Array<{ index: number; task: CapacityComparableTask }>>();
  currentTasks.forEach((task, index) => {
    const signature = taskSignature(task);
    const bucket = currentBuckets.get(signature) ?? [];
    bucket.push({ index, task });
    currentBuckets.set(signature, bucket);
  });

  const matchedCurrent = new Set<number>();
  const proposedChanges = proposedTasks.map((proposedTask, proposedIndex) => {
    const currentMatch = currentBuckets.get(taskSignature(proposedTask))?.shift();
    if (!currentMatch) {
      return capacityChangeView("added", proposedTask, proposedIndex);
    }
    matchedCurrent.add(currentMatch.index);
    if (proposedTask.estimatedMinutes < currentMatch.task.estimatedMinutes) {
      return capacityChangeView("shortened", proposedTask, proposedIndex, currentMatch.task);
    }
    if (proposedTask.estimatedMinutes > currentMatch.task.estimatedMinutes) {
      return capacityChangeView("extended", proposedTask, proposedIndex, currentMatch.task);
    }
    return capacityChangeView("retained", proposedTask, proposedIndex, currentMatch.task);
  });

  const removedChanges = currentTasks
    .map((task, index) => ({ task, index }))
    .filter(({ index }) => !matchedCurrent.has(index))
    .map(({ task, index }) => capacityChangeView("removed", task, proposedTasks.length + index));

  return [...proposedChanges, ...removedChanges];
}

export function plannedDuration(tasks: readonly CapacityComparableTask[]): string {
  const minutes = tasks.reduce((total, task) => total + task.estimatedMinutes, 0);
  return minutes === 0 ? "0 分钟" : durationLabel(minutes);
}

function taskSignature(task: CapacityComparableTask): string {
  return [task.track, task.kind, task.stage, task.title.trim()].join(":");
}

function capacityChangeView(
  kind: CapacityChangeKind,
  task: CapacityComparableTask,
  index: number,
  currentTask?: CapacityComparableTask,
): CapacityChangeView {
  const presentation = changePresentation[kind];
  return {
    id: `${kind}:${taskSignature(task)}:${String(index)}`,
    kind,
    tone: presentation.tone,
    label: presentation.label,
    title: task.title,
    duration: durationForChange(kind, task, currentTask),
    description: descriptionForChange(kind, task, currentTask),
  };
}

function durationForChange(
  kind: CapacityChangeKind,
  task: CapacityComparableTask,
  currentTask?: CapacityComparableTask,
): string {
  if ((kind === "shortened" || kind === "extended") && currentTask) {
    return `${durationLabel(currentTask.estimatedMinutes)} → ${durationLabel(task.estimatedMinutes)}`;
  }
  return durationLabel(task.estimatedMinutes);
}

function descriptionForChange(
  kind: CapacityChangeKind,
  task: CapacityComparableTask,
  currentTask?: CapacityComparableTask,
): string {
  if ((kind === "shortened" || kind === "extended") && currentTask) {
    return `由 ${durationLabel(currentTask.estimatedMinutes)} 调整为 ${durationLabel(task.estimatedMinutes)}。${task.reason}`;
  }
  if (kind === "added") return `新加入今天。${task.reason}`;
  if (kind === "removed") return `调整后不再列入今天。${task.reason}`;
  return task.reason;
}
