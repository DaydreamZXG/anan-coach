import type { EssayArtifactPlanTaskCommand } from "../../api/essay-intelligence";
import { parseEssayTaskStage, type EssayTaskStage } from "./capabilities";
import { isPendingEssayDraftingSubmission, type PendingEssayDraftingSubmission } from "./drafting";
import type { EssayFormDrafts } from "./forms";

export const ESSAY_PENDING_SCHEMA_VERSION = 3 as const;
export const ESSAY_PENDING_TTL_MS = 7 * 24 * 60 * 60 * 1_000;

const storageRoot = `gongkao.essay.pending.v${String(ESSAY_PENDING_SCHEMA_VERSION)}`;
const legacyStoragePrefixes = [
  "gongkao.essay.pendingCompletion.",
  "gongkao.essay.pendingDrafting.",
] as const;

type PendingKind = "artifact" | "drafting";

interface PendingEnvelope<T> {
  schemaVersion: typeof ESSAY_PENDING_SCHEMA_VERSION;
  ownerUserId: string;
  createdAtMs: number;
  expiresAtMs: number;
  payload: T;
}

export interface PendingPlanTaskCompletion {
  taskVersionId: string;
  stage: EssayTaskStage;
  planTaskId: string;
  idempotencyKey: string;
  command: EssayArtifactPlanTaskCommand;
  drafts: EssayFormDrafts;
}

export function storePendingPlanTaskCompletion(
  ownerUserId: string,
  value: PendingPlanTaskCompletion,
  nowMs = Date.now(),
): void {
  if (!isPendingPlanTaskCompletion(value, value)) {
    throw new Error("Refusing to persist an invalid essay artifact completion command");
  }
  storeEnvelope(ownerUserId, "artifact", value.planTaskId, value, nowMs);
}

export function loadPendingPlanTaskCompletion(
  ownerUserId: string,
  context: { taskVersionId: string; stage: EssayTaskStage; planTaskId: string },
  nowMs = Date.now(),
): PendingPlanTaskCompletion | null {
  return loadEnvelope(
    ownerUserId,
    "artifact",
    context.planTaskId,
    (value): value is PendingPlanTaskCompletion => isPendingPlanTaskCompletion(value, context),
    nowMs,
  );
}

export function clearPendingPlanTaskCompletion(ownerUserId: string, planTaskId: string): void {
  wx.removeStorageSync(storageKey(ownerUserId, "artifact", planTaskId));
}

export function storePendingDraftingSubmission(
  ownerUserId: string,
  value: PendingEssayDraftingSubmission,
  nowMs = Date.now(),
): void {
  if (
    !isPendingEssayDraftingSubmission(value, {
      taskVersionId: value.taskVersionId,
      planTaskId: value.planTaskId,
    })
  ) {
    throw new Error("Refusing to persist an invalid essay drafting recovery command");
  }
  storeEnvelope(ownerUserId, "drafting", value.planTaskId, value, nowMs);
}

export function loadPendingDraftingSubmission(
  ownerUserId: string,
  context: { taskVersionId: string; planTaskId: string },
  nowMs = Date.now(),
): PendingEssayDraftingSubmission | null {
  return loadEnvelope(
    ownerUserId,
    "drafting",
    context.planTaskId,
    (value): value is PendingEssayDraftingSubmission =>
      isPendingEssayDraftingSubmission(value, context),
    nowMs,
  );
}

export function clearPendingDraftingSubmission(ownerUserId: string, planTaskId: string): void {
  wx.removeStorageSync(storageKey(ownerUserId, "drafting", planTaskId));
}

/** Clears this account's pending data plus all legacy unscoped essay drafts. */
export function clearEssayPendingForUser(ownerUserId: string): void {
  const accountPrefix = `${storageRoot}.${encodeKeyPart(required(ownerUserId, "userId"))}.`;
  for (const key of storageKeys()) {
    if (key.startsWith(accountPrefix) || isLegacyKey(key)) wx.removeStorageSync(key);
  }
}

/** Used when no trustworthy local identity remains. */
export function clearAllEssayPending(): void {
  for (const key of storageKeys()) {
    if (key.startsWith(`${storageRoot}.`) || isLegacyKey(key)) wx.removeStorageSync(key);
  }
}

export function clearLegacyEssayPending(): void {
  for (const key of storageKeys()) {
    if (isLegacyKey(key)) wx.removeStorageSync(key);
  }
}

function storeEnvelope(
  ownerUserId: string,
  kind: PendingKind,
  planTaskId: string,
  payload: unknown,
  nowMs: number,
): void {
  const owner = required(ownerUserId, "userId");
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<unknown> = {
    schemaVersion: ESSAY_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ESSAY_PENDING_TTL_MS,
    payload,
  };
  wx.setStorageSync(storageKey(owner, kind, planTaskId), envelope);
}

function loadEnvelope<T>(
  ownerUserId: string,
  kind: PendingKind,
  planTaskId: string,
  validatePayload: (value: unknown) => value is T,
  nowMs: number,
): T | null {
  const owner = required(ownerUserId, "userId");
  const key = storageKey(owner, kind, planTaskId);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs)) || !validatePayload(value.payload)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

function storageKey(ownerUserId: string, kind: PendingKind, planTaskId: string): string {
  return `${storageRoot}.${encodeKeyPart(required(ownerUserId, "userId"))}.${kind}.${encodeKeyPart(required(planTaskId, "planTaskId"))}`;
}

function isEnvelope(
  value: unknown,
  ownerUserId: string,
  nowMs: number,
): value is PendingEnvelope<unknown> {
  if (!isRecord(value)) return false;
  return (
    value.schemaVersion === ESSAY_PENDING_SCHEMA_VERSION &&
    value.ownerUserId === ownerUserId &&
    Number.isSafeInteger(value.createdAtMs) &&
    Number.isSafeInteger(value.expiresAtMs) &&
    Number(value.createdAtMs) >= 0 &&
    Number(value.expiresAtMs) > Number(value.createdAtMs) &&
    Number(value.expiresAtMs) > nowMs &&
    value.payload !== undefined
  );
}

function isPendingPlanTaskCompletion(
  value: unknown,
  context: { taskVersionId: string; stage: EssayTaskStage; planTaskId: string },
): value is PendingPlanTaskCompletion {
  if (!isRecord(value) || !isRecord(value.command) || !isRecord(value.command.artifact)) {
    return false;
  }
  return (
    value.taskVersionId === context.taskVersionId &&
    value.stage === context.stage &&
    parseEssayTaskStage(value.stage as string | undefined) === value.stage &&
    value.planTaskId === context.planTaskId &&
    typeof value.idempotencyKey === "string" &&
    value.idempotencyKey.length >= 8 &&
    value.command.planTaskId === context.planTaskId &&
    value.command.artifact.taskVersionId === context.taskVersionId &&
    Number.isInteger(value.command.elapsedMinutes) &&
    Number(value.command.elapsedMinutes) > 0 &&
    isRecord(value.drafts)
  );
}

function storageKeys(): string[] {
  return wx.getStorageInfoSync().keys;
}

function isLegacyKey(key: string): boolean {
  return legacyStoragePrefixes.some((prefix) => key.startsWith(prefix));
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Essay pending storage requires ${label}`);
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("Essay pending storage requires a valid current timestamp");
  }
  return value;
}

function encodeKeyPart(value: string): string {
  return encodeURIComponent(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
