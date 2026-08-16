import type {
  XingceMasteryQueueActionState,
  XingceMasteryQueueBucket,
  XingceMasteryQueueItem,
  XingceMasteryQueueSummary,
} from "../../../api/xingce-learning";

export interface MasteryQueueTabView {
  bucket: XingceMasteryQueueBucket;
  label: string;
  count: number;
  active: boolean;
}

export interface MasteryQueueItemView {
  itemId: string;
  bucket: XingceMasteryQueueBucket;
  title: string;
  stateLabel: string;
  stageLabel: string;
  dateLabel: string;
  questionLabel: string;
  evidenceLabel: string;
  actionState: XingceMasteryQueueActionState;
  actionLabel: string;
  actionAvailable: boolean;
  invalidated: boolean;
  source: XingceMasteryQueueItem;
}

const tabLabels: Record<XingceMasteryQueueBucket, string> = {
  pending_reflection: "待复盘",
  due_verification: "待复测",
  stable_mastery: "稳定掌握",
  invalidated: "已撤回",
};

const emptyCopies: Record<XingceMasteryQueueBucket, { title: string; description: string }> = {
  pending_reflection: {
    title: "没有待复盘记录",
    description: "完成真实作答后，需要表达的复盘会出现在这里。",
  },
  due_verification: {
    title: "当前没有到期复测",
    description: "岸岸会按你的掌握情况和到期时间安排复测，不会临时拼题。",
  },
  stable_mastery: {
    title: "还没有稳定掌握记录",
    description: "通过即时、首次和延时验证后，稳定证据会保留在这里。",
  },
  invalidated: {
    title: "没有内容撤回记录",
    description: "被确认撤回的题目才会进入此处，并停止影响掌握判断。",
  },
};

export function masteryQueueTabs(
  summary: XingceMasteryQueueSummary,
  active: XingceMasteryQueueBucket,
): MasteryQueueTabView[] {
  const counts: Record<XingceMasteryQueueBucket, number> = {
    pending_reflection: summary.pendingReflectionCount,
    due_verification: summary.dueVerificationCount,
    stable_mastery: summary.stableMasteryCount,
    invalidated: summary.invalidatedCount,
  };
  return (Object.keys(tabLabels) as XingceMasteryQueueBucket[]).map((bucket) => ({
    bucket,
    label: tabLabels[bucket],
    count: counts[bucket],
    active: bucket === active,
  }));
}

export function presentMasteryQueueItem(item: XingceMasteryQueueItem): MasteryQueueItemView {
  const actionAvailable =
    item.bucket === "pending_reflection" || item.bucket === "due_verification";
  return {
    itemId: item.itemId,
    bucket: item.bucket,
    title: item.moduleName?.trim() || "行测训练",
    stateLabel: stateLabel(item),
    stageLabel: stageLabel(item),
    dateLabel: dateLabel(item),
    questionLabel: questionLabel(item),
    evidenceLabel: `${String(item.evidenceRefs.length)} 条相关学习记录`,
    actionState: item.action.state,
    actionLabel: actionLabel(item),
    actionAvailable,
    invalidated: item.bucket === "invalidated",
    source: item,
  };
}

export function preferredNonEmptyMasteryBucket(
  summary: XingceMasteryQueueSummary,
  current: XingceMasteryQueueBucket,
): XingceMasteryQueueBucket | null {
  const counts: Record<XingceMasteryQueueBucket, number> = {
    pending_reflection: summary.pendingReflectionCount,
    due_verification: summary.dueVerificationCount,
    stable_mastery: summary.stableMasteryCount,
    invalidated: summary.invalidatedCount,
  };
  const priority: XingceMasteryQueueBucket[] = [
    "pending_reflection",
    "due_verification",
    "stable_mastery",
    "invalidated",
  ];
  return priority.find((bucket) => bucket !== current && counts[bucket] > 0) ?? null;
}

export function masteryQueueEmptyCopy(bucket: XingceMasteryQueueBucket): {
  title: string;
  description: string;
} {
  return emptyCopies[bucket];
}

export function generatedAtLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "生成时间未知";
  return `更新于 ${two(date.getMonth() + 1)}.${two(date.getDate())} ${two(date.getHours())}:${two(date.getMinutes())}`;
}

function stateLabel(item: XingceMasteryQueueItem): string {
  if (item.bucket === "pending_reflection") return "复盘未完成";
  if (item.bucket === "due_verification") {
    return item.action.state === "awaiting_plan" ? "到期·等待计划" : "复测已到期";
  }
  if (item.bucket === "stable_mastery") return "稳定掌握";
  return "内容已撤回";
}

function stageLabel(item: XingceMasteryQueueItem): string {
  const labels: Record<string, string> = {
    immediate: "即时变式",
    immediate_verification: "即时变式",
    first_review: "首次复测",
    delayed_verification: "延时验证",
  };
  if (item.bucket === "pending_reflection") return "等待用户原话";
  if (item.bucket === "invalidated") return "不再进入掌握判断";
  return item.verificationStage
    ? (labels[item.verificationStage] ?? item.verificationStage)
    : "掌握证据";
}

function dateLabel(item: XingceMasteryQueueItem): string {
  const source = item.dueAt ?? item.createdAt;
  const date = new Date(source);
  if (Number.isNaN(date.getTime())) return "时间未知";
  const now = new Date();
  const sameDay =
    now.getFullYear() === date.getFullYear() &&
    now.getMonth() === date.getMonth() &&
    now.getDate() === date.getDate();
  if (item.dueAt && sameDay) return "今天到期";
  return `${two(date.getMonth() + 1)}月${two(date.getDate())}日`;
}

function actionLabel(item: XingceMasteryQueueItem): string {
  if (item.bucket === "pending_reflection") return "继续复盘";
  if (item.bucket === "due_verification") {
    return item.action.state === "awaiting_plan" ? "回到今日计划" : "开始本次复测";
  }
  return "";
}

function questionLabel(item: XingceMasteryQueueItem): string {
  const count = String(item.questionCount);
  if (item.bucket === "pending_reflection") return `本组 ${count} 道错题`;
  if (item.bucket === "due_verification") return `累计 ${count} 道待复测`;
  if (item.bucket === "stable_mastery") return `累计 ${count} 道已验证`;
  return `${count} 道已撤回`;
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}
