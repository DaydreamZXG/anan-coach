import type {
  XingceMasteryQueueAction,
  XingceMasteryQueueBucket,
  XingceMasteryQueueItem,
  XingceMasteryQueuePage,
  XingceMasteryQueueSummary,
} from "../../../api/xingce-learning";
import {
  registeredNavigationTarget,
  registeredTaskNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../../app/navigation/routes";

export const masteryQueueBuckets = [
  "pending_reflection",
  "due_verification",
  "stable_mastery",
  "invalidated",
] as const satisfies readonly XingceMasteryQueueBucket[];

export const emptyMasteryQueueSummary: XingceMasteryQueueSummary = {
  pendingReflectionCount: 0,
  dueVerificationCount: 0,
  stableMasteryCount: 0,
  invalidatedCount: 0,
};

export class XingceMasteryQueueContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "XingceMasteryQueueContractError";
  }
}

export type ValidatedXingceMasteryQueuePage = Omit<
  XingceMasteryQueuePage,
  "contractVersion" | "nextCursor"
> & {
  contractVersion: "xingce-mastery-queue@v1";
  nextCursor: string | null;
};

export function isMasteryQueueBucket(value: string): value is XingceMasteryQueueBucket {
  return masteryQueueBuckets.includes(value as XingceMasteryQueueBucket);
}

export function assertMasteryQueuePage(
  value: XingceMasteryQueuePage,
  expectedBucket: XingceMasteryQueueBucket,
): ValidatedXingceMasteryQueuePage {
  const contractVersion = value.contractVersion;
  if (contractVersion !== "xingce-mastery-queue@v1") {
    throw new XingceMasteryQueueContractError("当前小程序需要更新后才能查看掌握练习。");
  }
  if (value.bucket !== expectedBucket) {
    throw new XingceMasteryQueueContractError("掌握队列响应分桶与请求不一致。");
  }
  assertSummary(value.summary);
  assertTimestamp(value.generatedAt, "队列生成时间");
  if (value.nextCursor != null && !value.nextCursor.trim()) {
    throw new XingceMasteryQueueContractError("掌握队列下一页游标为空。");
  }
  const itemIds = new Set<string>();
  for (const item of value.items) {
    assertItem(item, expectedBucket);
    if (itemIds.has(item.itemId)) {
      throw new XingceMasteryQueueContractError("掌握队列单页包含重复项目。");
    }
    itemIds.add(item.itemId);
  }
  return {
    ...value,
    contractVersion,
    nextCursor: value.nextCursor ?? null,
  };
}

export function masteryQueueActionTarget(
  item: XingceMasteryQueueItem,
): RegisteredNavigationTarget | null {
  if (item.action.state !== "ready" || !item.action.startUrl) return null;
  const parsed = parseQuery(item.action.startUrl);
  if (!parsed) return null;

  if (item.bucket === "pending_reflection") {
    if (
      parsed.path !== routes.reflection ||
      !item.sessionId ||
      !item.reviewId ||
      parsed.values.size !== 2 ||
      parsed.values.get("sessionId") !== item.sessionId ||
      parsed.values.get("reviewId") !== item.reviewId
    ) {
      return null;
    }
    return registeredNavigationTarget(item.action.startUrl);
  }

  if (item.bucket === "due_verification") {
    if (
      parsed.path !== routes.xingceTaskIntro ||
      !item.action.planTaskId ||
      parsed.values.size !== 2 ||
      parsed.values.get("mode") !== "verification" ||
      parsed.values.get("planTaskId") !== item.action.planTaskId
    ) {
      return null;
    }
    return registeredTaskNavigationTarget(item.action.startUrl);
  }

  return null;
}

function assertSummary(summary: XingceMasteryQueueSummary): void {
  for (const value of Object.values(summary)) {
    if (!Number.isInteger(value) || value < 0) {
      throw new XingceMasteryQueueContractError("掌握队列摘要计数无效。");
    }
  }
}

function assertItem(item: XingceMasteryQueueItem, bucket: XingceMasteryQueueBucket): void {
  if (!item.itemId.trim() || item.bucket !== bucket || !item.state.trim()) {
    throw new XingceMasteryQueueContractError("掌握队列项目身份或状态无效。");
  }
  if (!Number.isInteger(item.questionCount) || item.questionCount < 1) {
    throw new XingceMasteryQueueContractError("掌握队列题目数量无效。");
  }
  if (item.evidenceRefs.length < 1 || item.evidenceRefs.some((ref) => !ref.trim())) {
    throw new XingceMasteryQueueContractError("掌握队列项目缺少可回放证据。");
  }
  assertTimestamp(item.createdAt, "项目创建时间");
  if (item.dueAt != null) assertTimestamp(item.dueAt, "复测到期时间");
  assertAction(item.action, bucket);

  if (bucket === "pending_reflection") {
    if (!item.sessionId || !item.reviewId || item.masteryId || item.masteryVersionId) {
      throw new XingceMasteryQueueContractError("待复盘项目缺少完整作答记录。");
    }
  } else if (!item.masteryId || !item.masteryVersionId || item.sessionId || item.reviewId) {
    throw new XingceMasteryQueueContractError("掌握项目缺少完整学习记录。");
  }

  if (
    (bucket === "invalidated" && item.invalidationReason !== "content_withdrawn") ||
    (bucket !== "invalidated" && (item.invalidationReason ?? null) !== null)
  ) {
    throw new XingceMasteryQueueContractError("内容撤回状态与撤回原因不一致。");
  }
}

function assertAction(action: XingceMasteryQueueAction, bucket: XingceMasteryQueueBucket): void {
  if (action.state === "ready") {
    if (!action.startUrl?.trim()) {
      throw new XingceMasteryQueueContractError("可执行队列项目缺少入口。");
    }
    if (bucket === "due_verification" && !action.planTaskId) {
      throw new XingceMasteryQueueContractError("可执行复测没有绑定计划任务。");
    }
    if (bucket === "stable_mastery" || bucket === "invalidated") {
      throw new XingceMasteryQueueContractError("不可执行分桶不应提供启动动作。");
    }
    return;
  }
  if ((action.startUrl ?? null) !== null || (action.planTaskId ?? null) !== null) {
    throw new XingceMasteryQueueContractError("非执行状态不应携带计划入口。");
  }
  if (action.state === "awaiting_plan" && bucket !== "due_verification") {
    throw new XingceMasteryQueueContractError("只有到期复测可以等待计划发布。");
  }
}

function assertTimestamp(value: string, label: string): void {
  if (!value.trim() || Number.isNaN(new Date(value).getTime())) {
    throw new XingceMasteryQueueContractError(`${label}无效。`);
  }
}

function parseQuery(url: string): { path: string; values: Map<string, string> } | null {
  const [path = "", rawQuery = ""] = url.split("?", 2);
  const values = new Map<string, string>();
  try {
    for (const pair of rawQuery.split("&").filter(Boolean)) {
      const [rawKey = "", rawValue = ""] = pair.split("=", 2);
      const key = decodeURIComponent(rawKey);
      if (!key || values.has(key)) return null;
      values.set(key, decodeURIComponent(rawValue));
    }
  } catch {
    return null;
  }
  return { path, values };
}
