import type {
  MockExamCheckpointRequest,
  MockExamSubmitRequest,
  MockExamTrajectoryEventCommand,
} from "../../api/generated/types.gen";

/** 本地暂存的 checkpoint 命令（保存结果未知时供恢复重放） */
export interface PendingMockCheckpoint {
  kind: "checkpoint";
  ownerUserId: string;
  sessionId: string;
  idempotencyKey: string;
  command: MockExamCheckpointRequest;
  storedAt: string;
}

/** 本地暂存的交卷命令（交卷结果未知时供恢复继续确认） */
export interface PendingMockSubmit {
  kind: "submit";
  ownerUserId: string;
  sessionId: string;
  idempotencyKey: string;
  command: MockExamSubmitRequest;
  storedAt: string;
}

/** 本地暂存的作答轨迹事件（按 eventSeq 排序逐条上行） */
export interface PendingMockTrajectoryEvent {
  ownerUserId: string;
  sessionId: string;
  idempotencyKey: string;
  command: MockExamTrajectoryEventCommand;
  storedAt: string;
}

const ROOT = "gongkao.mock-exam.pending.v1";
const TRAJECTORY_ROOT = "gongkao.mock-exam.trajectory.v1";

export function storePendingMockCheckpoint(value: PendingMockCheckpoint): void {
  assertEnvelope(value, "checkpoint");
  wx.setStorageSync(key(value.ownerUserId, value.sessionId, value.kind), value);
}

export function loadPendingMockCheckpoint(
  ownerUserId: string,
  sessionId: string,
): PendingMockCheckpoint | undefined {
  const storageKey = key(ownerUserId, sessionId, "checkpoint");
  const value: unknown = wx.getStorageSync(storageKey);
  if (!isCheckpoint(value, ownerUserId, sessionId)) {
    if (value) wx.removeStorageSync(storageKey);
    return undefined;
  }
  return value;
}

export function clearPendingMockCheckpoint(ownerUserId: string, sessionId: string): void {
  wx.removeStorageSync(key(ownerUserId, sessionId, "checkpoint"));
}

export function storePendingMockSubmit(value: PendingMockSubmit): void {
  assertEnvelope(value, "submit");
  wx.setStorageSync(key(value.ownerUserId, value.sessionId, value.kind), value);
}

export function loadPendingMockSubmit(
  ownerUserId: string,
  sessionId: string,
): PendingMockSubmit | undefined {
  const storageKey = key(ownerUserId, sessionId, "submit");
  const value: unknown = wx.getStorageSync(storageKey);
  if (!isSubmit(value, ownerUserId, sessionId)) {
    if (value) wx.removeStorageSync(storageKey);
    return undefined;
  }
  return value;
}

export function clearPendingMockSubmit(ownerUserId: string, sessionId: string): void {
  wx.removeStorageSync(key(ownerUserId, sessionId, "submit"));
}

export function appendPendingMockTrajectory(value: PendingMockTrajectoryEvent): void {
  assertTrajectory(value);
  const pending = loadPendingMockTrajectory(value.ownerUserId, value.sessionId);
  const replay = pending.find((item) => item.command.eventSeq === value.command.eventSeq);
  if (replay && replay.idempotencyKey !== value.idempotencyKey) {
    throw new Error("待上行轨迹序号已被占用");
  }
  if (!replay) pending.push(value);
  storeTrajectory(value.ownerUserId, value.sessionId, pending);
}

export function loadPendingMockTrajectory(
  ownerUserId: string,
  sessionId: string,
): PendingMockTrajectoryEvent[] {
  const storageKey = trajectoryKey(ownerUserId, sessionId);
  const value: unknown = wx.getStorageSync(storageKey);
  if (value === undefined || value === null || value === "") return [];
  if (!Array.isArray(value)) {
    wx.removeStorageSync(storageKey);
    return [];
  }
  const items: unknown[] = value;
  if (items.some((item) => !isTrajectory(item, ownerUserId, sessionId))) {
    wx.removeStorageSync(storageKey);
    return [];
  }
  return items
    .filter((item): item is PendingMockTrajectoryEvent =>
      isTrajectory(item, ownerUserId, sessionId),
    )
    .sort((left, right) => left.command.eventSeq - right.command.eventSeq);
}

export function replacePendingMockTrajectory(
  ownerUserId: string,
  sessionId: string,
  values: PendingMockTrajectoryEvent[],
): void {
  values.forEach(assertTrajectory);
  if (values.some((value) => value.ownerUserId !== ownerUserId || value.sessionId !== sessionId)) {
    throw new Error("待上行轨迹归属不能变更");
  }
  storeTrajectory(ownerUserId, sessionId, values);
}

export function removePendingMockTrajectory(
  ownerUserId: string,
  sessionId: string,
  idempotencyKey: string,
): void {
  const remaining = loadPendingMockTrajectory(ownerUserId, sessionId).filter(
    (item) => item.idempotencyKey !== idempotencyKey,
  );
  storeTrajectory(ownerUserId, sessionId, remaining);
}

function key(ownerUserId: string, sessionId: string, kind: "checkpoint" | "submit"): string {
  return `${ROOT}.${encode(required(ownerUserId, "ownerUserId"))}.${encode(required(sessionId, "sessionId"))}.${kind}`;
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function trajectoryKey(ownerUserId: string, sessionId: string): string {
  return `${TRAJECTORY_ROOT}.${encode(required(ownerUserId, "ownerUserId"))}.${encode(required(sessionId, "sessionId"))}`;
}

function storeTrajectory(
  ownerUserId: string,
  sessionId: string,
  values: PendingMockTrajectoryEvent[],
): void {
  const storageKey = trajectoryKey(ownerUserId, sessionId);
  if (values.length === 0) {
    wx.removeStorageSync(storageKey);
    return;
  }
  wx.setStorageSync(storageKey, values);
}

function assertEnvelope(
  value: PendingMockCheckpoint | PendingMockSubmit,
  kind: "checkpoint" | "submit",
): void {
  if (value.kind !== kind) throw new Error("本地暂存命令类型无效");
  required(value.ownerUserId, "ownerUserId");
  required(value.sessionId, "sessionId");
  required(value.idempotencyKey, "idempotencyKey");
  if (!Number.isFinite(Date.parse(value.storedAt))) {
    throw new Error("本地暂存时间无效");
  }
}

function isCheckpoint(
  value: unknown,
  ownerUserId: string,
  sessionId: string,
): value is PendingMockCheckpoint {
  if (!baseMatches(value, "checkpoint", ownerUserId, sessionId)) return false;
  const command = (value as { command?: unknown }).command;
  return (
    isRecord(command) &&
    Number.isInteger(command.expectedRevision) &&
    Array.isArray(command.answers)
  );
}

function isSubmit(
  value: unknown,
  ownerUserId: string,
  sessionId: string,
): value is PendingMockSubmit {
  if (!baseMatches(value, "submit", ownerUserId, sessionId)) return false;
  const command = (value as { command?: unknown }).command;
  return (
    isRecord(command) &&
    Number.isInteger(command.expectedRevision) &&
    Array.isArray(command.answers)
  );
}

function assertTrajectory(value: PendingMockTrajectoryEvent): void {
  required(value.ownerUserId, "ownerUserId");
  required(value.sessionId, "sessionId");
  required(value.idempotencyKey, "idempotencyKey");
  if (!Number.isFinite(Date.parse(value.storedAt))) {
    throw new Error("待上行轨迹暂存时间无效");
  }
  if (!Number.isInteger(value.command.eventSeq) || value.command.eventSeq < 1) {
    throw new Error("待上行轨迹序号无效");
  }
  if (!Number.isInteger(value.command.sessionRevision) || value.command.sessionRevision < 1) {
    throw new Error("待上行轨迹版本号无效");
  }
}

function isTrajectory(
  value: unknown,
  ownerUserId: string,
  sessionId: string,
): value is PendingMockTrajectoryEvent {
  if (!isRecord(value)) return false;
  const command = value.command;
  if (!isRecord(command)) return false;
  return (
    value.ownerUserId === ownerUserId &&
    value.sessionId === sessionId &&
    typeof value.idempotencyKey === "string" &&
    typeof value.storedAt === "string" &&
    Number.isFinite(Date.parse(value.storedAt)) &&
    Number.isInteger(command.eventSeq) &&
    Number(command.eventSeq) >= 1 &&
    typeof command.eventType === "string" &&
    (typeof command.questionId === "string" ||
      command.questionId === null ||
      command.questionId === undefined) &&
    typeof command.clientOccurredAt === "string" &&
    Number.isFinite(Date.parse(command.clientOccurredAt)) &&
    Number.isInteger(command.sessionRevision)
  );
}

function baseMatches(
  value: unknown,
  kind: "checkpoint" | "submit",
  ownerUserId: string,
  sessionId: string,
): boolean {
  if (!isRecord(value)) return false;
  return (
    value.kind === kind &&
    value.ownerUserId === ownerUserId &&
    value.sessionId === sessionId &&
    typeof value.idempotencyKey === "string" &&
    typeof value.storedAt === "string" &&
    Number.isFinite(Date.parse(value.storedAt))
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`本地暂存命令缺少 ${label}`);
  return normalized;
}
