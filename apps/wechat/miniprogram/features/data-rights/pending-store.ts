const SCHEMA_VERSION = 1 as const;
const PENDING_TTL_MS = 7 * 24 * 60 * 60 * 1_000;
const storageRoot = `gongkao.data-rights.pending.v${String(SCHEMA_VERSION)}`;

interface PendingEnvelope<T> {
  schemaVersion: typeof SCHEMA_VERSION;
  ownerUserId: string;
  createdAtMs: number;
  expiresAtMs: number;
  payload: T;
}

export interface PendingDataExport {
  idempotencyKey: string;
  exportRequestId: string;
}

export interface PendingAccountDeletion {
  idempotencyKey: string;
}

export function storePendingDataExport(
  ownerUserId: string,
  payload: PendingDataExport,
  nowMs = Date.now(),
): void {
  storeEnvelope(exportStorageKey, ownerUserId, payload, isPendingDataExport, nowMs);
}

export function loadPendingDataExport(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingDataExport | null {
  return loadEnvelope(exportStorageKey, ownerUserId, isPendingDataExport, nowMs);
}

export function clearPendingDataExport(ownerUserId: string): void {
  wx.removeStorageSync(exportStorageKey(required(ownerUserId)));
}

export function storePendingAccountDeletion(
  ownerUserId: string,
  payload: PendingAccountDeletion,
  nowMs = Date.now(),
): void {
  storeEnvelope(deletionStorageKey, ownerUserId, payload, isPendingAccountDeletion, nowMs);
}

export function loadPendingAccountDeletion(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingAccountDeletion | null {
  return loadEnvelope(deletionStorageKey, ownerUserId, isPendingAccountDeletion, nowMs);
}

export function clearPendingAccountDeletion(ownerUserId: string): void {
  wx.removeStorageSync(deletionStorageKey(required(ownerUserId)));
}

export function clearDataRightsPendingForUser(ownerUserId: string): void {
  const owner = required(ownerUserId);
  wx.removeStorageSync(exportStorageKey(owner));
  wx.removeStorageSync(deletionStorageKey(owner));
}

export function clearAllDataRightsPending(): void {
  for (const key of wx.getStorageInfoSync().keys) {
    if (key.startsWith(`${storageRoot}.`)) wx.removeStorageSync(key);
  }
}

function storeEnvelope<T>(
  storageKey: (ownerUserId: string) => string,
  ownerUserId: string,
  payload: T,
  validate: (value: unknown) => value is T,
  nowMs: number,
): void {
  const owner = required(ownerUserId);
  if (!validate(payload)) throw new Error("Refusing to persist an invalid data-rights command");
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<T> = {
    schemaVersion: SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + PENDING_TTL_MS,
    payload,
  };
  wx.setStorageSync(storageKey(owner), envelope);
}

function loadEnvelope<T>(
  storageKey: (ownerUserId: string) => string,
  ownerUserId: string,
  validate: (value: unknown) => value is T,
  nowMs: number,
): T | null {
  const owner = required(ownerUserId);
  const key = storageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validate, validNow(nowMs))) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

function isEnvelope<T>(
  value: unknown,
  ownerUserId: string,
  validate: (value: unknown) => value is T,
  nowMs: number,
): value is PendingEnvelope<T> {
  if (!isRecord(value)) return false;
  return (
    value.schemaVersion === SCHEMA_VERSION &&
    value.ownerUserId === ownerUserId &&
    Number.isSafeInteger(value.createdAtMs) &&
    Number.isSafeInteger(value.expiresAtMs) &&
    Number(value.createdAtMs) >= 0 &&
    Number(value.expiresAtMs) > Number(value.createdAtMs) &&
    Number(value.expiresAtMs) > nowMs &&
    validate(value.payload)
  );
}

function isPendingDataExport(value: unknown): value is PendingDataExport {
  return (
    isRecord(value) &&
    validCommandKey(value.idempotencyKey) &&
    typeof value.exportRequestId === "string"
  );
}

function isPendingAccountDeletion(value: unknown): value is PendingAccountDeletion {
  return isRecord(value) && validCommandKey(value.idempotencyKey);
}

function validCommandKey(value: unknown): value is string {
  return typeof value === "string" && value.length >= 8 && value.length <= 160;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function exportStorageKey(ownerUserId: string): string {
  return `${storageRoot}.export.${encodeURIComponent(ownerUserId)}`;
}

function deletionStorageKey(ownerUserId: string): string {
  return `${storageRoot}.deletion.${encodeURIComponent(ownerUserId)}`;
}

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Data-rights pending storage requires userId");
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("Data-rights pending storage requires a valid timestamp");
  }
  return value;
}
