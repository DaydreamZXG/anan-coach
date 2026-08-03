import type { QualificationFactInput } from "../../api/generated/types.gen";
import {
  qualificationFactsFingerprint,
  qualificationFactsFromDraft,
  validateQualificationDraft,
  type QualificationDraft,
} from "./onboarding";

const SCHEMA_VERSION = 2 as const;
const TTL_MS = 7 * 24 * 60 * 60 * 1_000;
const storageFamilyRoot = "gongkao.qualification.profile-edit.pending";
const storageRoot = `${storageFamilyRoot}.v${String(SCHEMA_VERSION)}`;

interface PendingEnvelope {
  schemaVersion: typeof SCHEMA_VERSION;
  ownerUserId: string;
  createdAtMs: number;
  expiresAtMs: number;
  payload: PendingQualificationProfileEdit;
}

export interface PendingQualificationProfileEdit {
  idempotencyKey: string;
  draft: QualificationDraft;
  facts: QualificationFactInput[];
}

export function storePendingQualificationProfileEdit(
  ownerUserId: string,
  value: PendingQualificationProfileEdit,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId);
  if (!isPendingQualificationProfileEdit(value)) {
    throw new Error("Refusing to persist an invalid qualification profile edit command");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope = {
    schemaVersion: SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + TTL_MS,
    payload: value,
  };
  wx.setStorageSync(storageKey(owner), envelope);
}

export function loadPendingQualificationProfileEdit(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingQualificationProfileEdit | null {
  const owner = required(ownerUserId);
  const key = storageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs))) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingQualificationProfileEdit(ownerUserId: string): void {
  wx.removeStorageSync(storageKey(required(ownerUserId)));
}

export function clearAllQualificationProfileEditPending(): void {
  for (const key of wx.getStorageInfoSync().keys) {
    if (key.startsWith(`${storageFamilyRoot}.v`)) wx.removeStorageSync(key);
  }
}

function isEnvelope(value: unknown, ownerUserId: string, nowMs: number): value is PendingEnvelope {
  if (!isRecord(value)) return false;
  return (
    value.schemaVersion === SCHEMA_VERSION &&
    value.ownerUserId === ownerUserId &&
    Number.isSafeInteger(value.createdAtMs) &&
    Number.isSafeInteger(value.expiresAtMs) &&
    Number(value.createdAtMs) >= 0 &&
    Number(value.expiresAtMs) > Number(value.createdAtMs) &&
    Number(value.expiresAtMs) > nowMs &&
    isPendingQualificationProfileEdit(value.payload)
  );
}

function isPendingQualificationProfileEdit(
  value: unknown,
): value is PendingQualificationProfileEdit {
  if (!isRecord(value) || !isRecord(value.draft)) return false;
  if (typeof value.idempotencyKey !== "string" || value.idempotencyKey.length < 8) return false;
  const draft: unknown = value.draft;
  if (!isQualificationDraft(draft) || validateQualificationDraft(draft)) return false;
  return (
    Array.isArray(value.facts) &&
    value.facts.every(isQualificationFactInput) &&
    qualificationFactsFingerprint(value.facts) ===
      qualificationFactsFingerprint(qualificationFactsFromDraft(draft))
  );
}

function isQualificationDraft(value: unknown): value is QualificationDraft {
  if (!isRecord(value)) return false;
  return (
    typeof value.education === "string" &&
    typeof value.degree === "string" &&
    typeof value.major === "string" &&
    Number.isSafeInteger(value.graduationYear) &&
    (value.freshGraduate === "" ||
      value.freshGraduate === "true" ||
      value.freshGraduate === "false") &&
    typeof value.gender === "string" &&
    typeof value.politicalStatus === "string" &&
    Array.isArray(value.certifications) &&
    value.certifications.every((item) => typeof item === "string") &&
    Number.isSafeInteger(value.workYears) &&
    Number.isSafeInteger(value.grassrootsYears) &&
    typeof value.serviceProject === "string" &&
    typeof value.hukou === "string"
  );
}

function isQualificationFactInput(value: unknown): value is QualificationFactInput {
  if (!isRecord(value)) return false;
  return (
    typeof value.key === "string" &&
    value.key.length > 0 &&
    (typeof value.value === "string" ||
      typeof value.value === "number" ||
      typeof value.value === "boolean")
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function storageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}`;
}

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Qualification profile edit pending storage requires userId");
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("Qualification profile edit pending storage requires a valid timestamp");
  }
  return value;
}
