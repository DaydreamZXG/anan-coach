import type {
  MockExamCheckpointCommand,
  MockExamSubmitCommand,
  MockExamTrajectoryCommand,
} from "../../api/mock-exam";

export interface PendingMockCheckpoint {
  kind: "checkpoint";
  ownerUserId: string;
  sessionId: string;
  idempotencyKey: string;
  command: MockExamCheckpointCommand;
  storedAt: string;
}

export interface PendingMockSubmit {
  kind: "submit";
  ownerUserId: string;
  sessionId: string;
  idempotencyKey: string;
  command: MockExamSubmitCommand;
  storedAt: string;
}

const ROOT = "gongkao.mock-exam.pending.v1";
const TRAJECTORY_ROOT = "gongkao.mock-exam.trajectory.v1";

export interface PendingMockTrajectoryEvent {
  ownerUserId: string;
  sessionId: string;
  idempotencyKey: string;
  command: MockExamTrajectoryCommand;
  storedAt: string;
}

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
    throw new Error("Pending mock trajectory sequence is already occupied");
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
    throw new Error("Pending mock trajectory ownership changed");
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
  if (value.kind !== kind) throw new Error("Pending mock command kind is invalid");
  required(value.ownerUserId, "ownerUserId");
  required(value.sessionId, "sessionId");
  required(value.idempotencyKey, "idempotencyKey");
  if (!Number.isFinite(Date.parse(value.storedAt)))
    throw new Error("Pending mock storedAt is invalid");
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
    throw new Error("Pending mock trajectory storedAt is invalid");
  }
  if (!Number.isInteger(value.command.eventSeq) || value.command.eventSeq < 1) {
    throw new Error("Pending mock trajectory sequence is invalid");
  }
  if (!Number.isInteger(value.command.sessionRevision) || value.command.sessionRevision < 1) {
    throw new Error("Pending mock trajectory revision is invalid");
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
    (typeof command.questionId === "string" || command.questionId === null) &&
    typeof command.clientOccurredAt === "string" &&
    Number.isFinite(Date.parse(command.clientOccurredAt)) &&
    Number.isInteger(command.sessionRevision) &&
    isRecord(command.payload)
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
  if (!normalized) throw new Error(`Pending mock command requires ${label}`);
  return normalized;
}
