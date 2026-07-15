import type {
  XingceCauseDecisionCommand,
  XingceCheckpointCommand,
  XingceReflectionCommand,
  XingceSubmitCommand,
  XingceTaskMode,
} from "../../../api/xingce-learning";

const schemaVersion = 1 as const;
const storageRoot = `gongkao.xingce.real-attempt.pending.v${String(schemaVersion)}`;

export interface PendingXingceStart {
  kind: "start";
  planTaskId: string;
  mode: XingceTaskMode;
  idempotencyKey: string;
}

export interface PendingXingceCheckpoint {
  kind: "checkpoint";
  planTaskId: string;
  sessionId: string;
  idempotencyKey: string;
  command: XingceCheckpointCommand;
}

export interface PendingXingceSubmit {
  kind: "submit";
  planTaskId: string;
  sessionId: string;
  idempotencyKey: string;
  command: XingceSubmitCommand;
}

export interface PendingXingceReflection {
  kind: "reflection";
  sessionId: string;
  reviewId: string;
  idempotencyKey: string;
  command: XingceReflectionCommand;
}

export interface PendingXingceCauseDecision {
  kind: "cause_decision";
  sessionId: string;
  reviewId: string;
  idempotencyKey: string;
  command: XingceCauseDecisionCommand;
}

type PendingXingceCommand =
  | PendingXingceStart
  | PendingXingceCheckpoint
  | PendingXingceSubmit
  | PendingXingceReflection
  | PendingXingceCauseDecision;

export interface XingcePendingStore {
  storeStart(ownerUserId: string, pending: PendingXingceStart): void;
  loadStart(ownerUserId: string, planTaskId: string): PendingXingceStart | null;
  clearStart(ownerUserId: string, planTaskId: string): void;
  storeCheckpoint(ownerUserId: string, pending: PendingXingceCheckpoint): void;
  loadCheckpoint(ownerUserId: string, sessionId: string): PendingXingceCheckpoint | null;
  clearCheckpoint(ownerUserId: string, sessionId: string): void;
  storeSubmit(ownerUserId: string, pending: PendingXingceSubmit): void;
  loadSubmit(ownerUserId: string, sessionId: string): PendingXingceSubmit | null;
  clearSubmit(ownerUserId: string, sessionId: string): void;
  storeReflection(ownerUserId: string, pending: PendingXingceReflection): void;
  loadReflection(ownerUserId: string, reviewId: string): PendingXingceReflection | null;
  clearReflection(ownerUserId: string, reviewId: string): void;
  storeCauseDecision(ownerUserId: string, pending: PendingXingceCauseDecision): void;
  loadCauseDecision(ownerUserId: string, reviewId: string): PendingXingceCauseDecision | null;
  clearCauseDecision(ownerUserId: string, reviewId: string): void;
}

interface PendingEnvelope {
  schemaVersion: typeof schemaVersion;
  ownerUserId: string;
  storedAt: string;
  payload: PendingXingceCommand;
}

export function storePendingXingceStart(ownerUserId: string, pending: PendingXingceStart): void {
  store(ownerUserId, "start", pending.planTaskId, pending);
}

export function loadPendingXingceStart(
  ownerUserId: string,
  planTaskId: string,
): PendingXingceStart | null {
  return load(ownerUserId, "start", planTaskId, isPendingStart);
}

export function clearPendingXingceStart(ownerUserId: string, planTaskId: string): void {
  clear(ownerUserId, "start", planTaskId);
}

export function storePendingXingceCheckpoint(
  ownerUserId: string,
  pending: PendingXingceCheckpoint,
): void {
  store(ownerUserId, "checkpoint", pending.sessionId, pending);
}

export function loadPendingXingceCheckpoint(
  ownerUserId: string,
  sessionId: string,
): PendingXingceCheckpoint | null {
  return load(ownerUserId, "checkpoint", sessionId, isPendingCheckpoint);
}

export function clearPendingXingceCheckpoint(ownerUserId: string, sessionId: string): void {
  clear(ownerUserId, "checkpoint", sessionId);
}

export function storePendingXingceSubmit(ownerUserId: string, pending: PendingXingceSubmit): void {
  store(ownerUserId, "submit", pending.sessionId, pending);
}

export function loadPendingXingceSubmit(
  ownerUserId: string,
  sessionId: string,
): PendingXingceSubmit | null {
  return load(ownerUserId, "submit", sessionId, isPendingSubmit);
}

export function clearPendingXingceSubmit(ownerUserId: string, sessionId: string): void {
  clear(ownerUserId, "submit", sessionId);
}

export function storePendingXingceReflection(
  ownerUserId: string,
  pending: PendingXingceReflection,
): void {
  store(ownerUserId, "reflection", pending.reviewId, pending);
}

export function loadPendingXingceReflection(
  ownerUserId: string,
  reviewId: string,
): PendingXingceReflection | null {
  return load(ownerUserId, "reflection", reviewId, isPendingReflection);
}

export function clearPendingXingceReflection(ownerUserId: string, reviewId: string): void {
  clear(ownerUserId, "reflection", reviewId);
}

export function storePendingXingceCauseDecision(
  ownerUserId: string,
  pending: PendingXingceCauseDecision,
): void {
  store(ownerUserId, "cause-decision", pending.reviewId, pending);
}

export function loadPendingXingceCauseDecision(
  ownerUserId: string,
  reviewId: string,
): PendingXingceCauseDecision | null {
  return load(ownerUserId, "cause-decision", reviewId, isPendingCauseDecision);
}

export function clearPendingXingceCauseDecision(ownerUserId: string, reviewId: string): void {
  clear(ownerUserId, "cause-decision", reviewId);
}

export function clearXingcePendingForUser(ownerUserId: string): void {
  const prefix = `${storageRoot}.${encode(required(ownerUserId, "userId"))}.`;
  for (const key of storageKeys()) if (key.startsWith(prefix)) wx.removeStorageSync(key);
}

export function clearAllXingcePending(): void {
  for (const key of storageKeys()) {
    if (key.startsWith(`${storageRoot}.`)) wx.removeStorageSync(key);
  }
}

export const wechatXingcePendingStore: XingcePendingStore = {
  storeStart: storePendingXingceStart,
  loadStart: loadPendingXingceStart,
  clearStart: clearPendingXingceStart,
  storeCheckpoint: storePendingXingceCheckpoint,
  loadCheckpoint: loadPendingXingceCheckpoint,
  clearCheckpoint: clearPendingXingceCheckpoint,
  storeSubmit: storePendingXingceSubmit,
  loadSubmit: loadPendingXingceSubmit,
  clearSubmit: clearPendingXingceSubmit,
  storeReflection: storePendingXingceReflection,
  loadReflection: loadPendingXingceReflection,
  clearReflection: clearPendingXingceReflection,
  storeCauseDecision: storePendingXingceCauseDecision,
  loadCauseDecision: loadPendingXingceCauseDecision,
  clearCauseDecision: clearPendingXingceCauseDecision,
};

function store(
  ownerUserId: string,
  kind: string,
  targetId: string,
  payload: PendingXingceCommand,
): void {
  const owner = required(ownerUserId, "userId");
  const envelope: PendingEnvelope = {
    schemaVersion,
    ownerUserId: owner,
    storedAt: new Date().toISOString(),
    payload,
  };
  wx.setStorageSync(key(owner, kind, targetId), envelope);
}

function load<T extends PendingXingceCommand>(
  ownerUserId: string,
  kind: string,
  targetId: string,
  validate: (value: unknown) => value is T,
): T | null {
  const owner = required(ownerUserId, "userId");
  const storageKey = key(owner, kind, targetId);
  const value: unknown = wx.getStorageSync(storageKey);
  if (value === "" || value === undefined || value === null) return null;
  if (!isEnvelope(value, owner) || !validate(value.payload)) {
    wx.removeStorageSync(storageKey);
    return null;
  }
  return value.payload;
}

function clear(ownerUserId: string, kind: string, targetId: string): void {
  wx.removeStorageSync(key(required(ownerUserId, "userId"), kind, targetId));
}

function key(ownerUserId: string, kind: string, targetId: string): string {
  return `${storageRoot}.${encode(ownerUserId)}.${kind}.${encode(required(targetId, "targetId"))}`;
}

function isEnvelope(value: unknown, ownerUserId: string): value is PendingEnvelope {
  return (
    isRecord(value) &&
    value.schemaVersion === schemaVersion &&
    value.ownerUserId === ownerUserId &&
    typeof value.storedAt === "string" &&
    !Number.isNaN(new Date(value.storedAt).getTime()) &&
    isRecord(value.payload)
  );
}

function isPendingStart(value: unknown): value is PendingXingceStart {
  return (
    base(value, "start") && typeof value.planTaskId === "string" && typeof value.mode === "string"
  );
}

function isPendingCheckpoint(value: unknown): value is PendingXingceCheckpoint {
  return baseSessionCommand(value, "checkpoint") && isRecord(value.command);
}

function isPendingSubmit(value: unknown): value is PendingXingceSubmit {
  return baseSessionCommand(value, "submit") && isRecord(value.command);
}

function isPendingReflection(value: unknown): value is PendingXingceReflection {
  return baseReviewCommand(value, "reflection") && isRecord(value.command);
}

function isPendingCauseDecision(value: unknown): value is PendingXingceCauseDecision {
  return baseReviewCommand(value, "cause_decision") && isRecord(value.command);
}

function base(
  value: unknown,
  kind: PendingXingceCommand["kind"],
): value is Record<string, unknown> {
  return (
    isRecord(value) &&
    value.kind === kind &&
    typeof value.idempotencyKey === "string" &&
    value.idempotencyKey.length >= 8
  );
}

function baseSessionCommand(
  value: unknown,
  kind: "checkpoint" | "submit",
): value is Record<string, unknown> {
  return (
    base(value, kind) && typeof value.planTaskId === "string" && typeof value.sessionId === "string"
  );
}

function baseReviewCommand(
  value: unknown,
  kind: "reflection" | "cause_decision",
): value is Record<string, unknown> {
  return (
    base(value, kind) && typeof value.sessionId === "string" && typeof value.reviewId === "string"
  );
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Xingce pending command requires ${label}`);
  return normalized;
}

function encode(value: string): string {
  return encodeURIComponent(value);
}

function storageKeys(): string[] {
  return wx.getStorageInfoSync().keys;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
