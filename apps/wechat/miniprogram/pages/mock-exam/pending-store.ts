import type { MockExamCheckpointCommand, MockExamSubmitCommand } from "../../api/mock-exam";

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

function key(ownerUserId: string, sessionId: string, kind: "checkpoint" | "submit"): string {
  return `${ROOT}.${encode(required(ownerUserId, "ownerUserId"))}.${encode(required(sessionId, "sessionId"))}.${kind}`;
}

function encode(value: string): string {
  return encodeURIComponent(value);
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
