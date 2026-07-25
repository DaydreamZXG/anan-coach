import type {
  AbilityBaselineWrite,
  OnboardingResultAcknowledgement,
  PositionPreferenceWrite,
  PreparationExperienceWrite,
  QualificationFactInput,
  QualificationMilestoneCommand,
} from "../../api/generated/types.gen";
import { abilityBaselineDraftFromCommand, buildAbilityBaselineCommand } from "./ability-baseline";
import {
  buildPositionPreferenceCommand,
  type PositionPreferenceDraft,
} from "./position-preference";
import {
  buildPreparationExperienceCommand,
  validatePreparationExperience,
  type PreparationExperienceDraft,
} from "./preparation-experience";
import {
  qualificationFactsFingerprint,
  qualificationFactsFromDraft,
  validateOnboardingQualificationDraft,
  type QualificationDraft,
} from "../qualification/onboarding";
import {
  buildStudyPreferenceCommand,
  type StudyPreferenceCommandPayload,
  type StudyPreferenceDraft,
} from "./study-preference";

export const ONBOARDING_PENDING_SCHEMA_VERSION = 1 as const;
export const ONBOARDING_PENDING_TTL_MS = 7 * 24 * 60 * 60 * 1_000;

const storageRoot = `gongkao.onboarding.pending.v${String(ONBOARDING_PENDING_SCHEMA_VERSION)}`;

interface PendingEnvelope<T> {
  schemaVersion: typeof ONBOARDING_PENDING_SCHEMA_VERSION;
  ownerUserId: string;
  createdAtMs: number;
  expiresAtMs: number;
  payload: T;
}

export interface PendingStudyPreferenceCommand {
  idempotencyKey: string;
  draft: StudyPreferenceDraft;
  command: StudyPreferenceCommandPayload;
}

export interface PendingPreparationExperienceCommand {
  idempotencyKey: string;
  draft: PreparationExperienceDraft;
  command: PreparationExperienceWrite;
}

export interface PendingPositionPreferenceCommand {
  idempotencyKey: string;
  draft: PositionPreferenceDraft;
  command: PositionPreferenceWrite;
}

export interface PendingAbilityBaselineCommand {
  idempotencyKey: string;
  command: AbilityBaselineWrite;
}

export interface PendingQualificationProfileCommand {
  phase: "profile";
  idempotencyKey: string;
  draft: QualificationDraft;
  facts: QualificationFactInput[];
}

export interface PendingQualificationMilestoneCommand {
  phase: "milestone";
  idempotencyKey: string;
  draft: QualificationDraft;
  command: QualificationMilestoneCommand;
}

export type PendingQualificationCommand =
  PendingQualificationProfileCommand | PendingQualificationMilestoneCommand;

export interface PendingResultAcknowledgement {
  idempotencyKey: string;
  command: OnboardingResultAcknowledgement;
}

export function storePendingPreparationExperience(
  ownerUserId: string,
  value: PendingPreparationExperienceCommand,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  if (!isPendingPreparationExperienceCommand(value)) {
    throw new Error("Refusing to persist an invalid preparation-experience command");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<PendingPreparationExperienceCommand> = {
    schemaVersion: ONBOARDING_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ONBOARDING_PENDING_TTL_MS,
    payload: value,
  };
  wx.setStorageSync(preparationExperienceStorageKey(owner), envelope);
}

export function loadPendingPreparationExperience(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingPreparationExperienceCommand | null {
  const owner = required(ownerUserId, "userId");
  const key = preparationExperienceStorageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs), isPendingPreparationExperienceCommand)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingPreparationExperience(ownerUserId: string): void {
  wx.removeStorageSync(preparationExperienceStorageKey(required(ownerUserId, "userId")));
}

export function storePendingQualification(
  ownerUserId: string,
  value: PendingQualificationCommand,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  if (!isPendingQualificationCommand(value)) {
    throw new Error("Refusing to persist an invalid qualification command");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<PendingQualificationCommand> = {
    schemaVersion: ONBOARDING_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ONBOARDING_PENDING_TTL_MS,
    payload: value,
  };
  wx.setStorageSync(qualificationStorageKey(owner), envelope);
}

export function loadPendingQualification(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingQualificationCommand | null {
  const owner = required(ownerUserId, "userId");
  const key = qualificationStorageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs), isPendingQualificationCommand)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingQualification(ownerUserId: string): void {
  wx.removeStorageSync(qualificationStorageKey(required(ownerUserId, "userId")));
}

export function storePendingPositionPreference(
  ownerUserId: string,
  value: PendingPositionPreferenceCommand,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  if (!isPendingPositionPreferenceCommand(value)) {
    throw new Error("Refusing to persist an invalid position-preference command");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<PendingPositionPreferenceCommand> = {
    schemaVersion: ONBOARDING_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ONBOARDING_PENDING_TTL_MS,
    payload: value,
  };
  wx.setStorageSync(positionPreferenceStorageKey(owner), envelope);
}

export function loadPendingPositionPreference(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingPositionPreferenceCommand | null {
  const owner = required(ownerUserId, "userId");
  const key = positionPreferenceStorageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs), isPendingPositionPreferenceCommand)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingPositionPreference(ownerUserId: string): void {
  wx.removeStorageSync(positionPreferenceStorageKey(required(ownerUserId, "userId")));
}

export function storePendingAbilityBaseline(
  ownerUserId: string,
  value: PendingAbilityBaselineCommand,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  if (!isPendingAbilityBaselineCommand(value)) {
    throw new Error("Refusing to persist an invalid ability-baseline command");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<PendingAbilityBaselineCommand> = {
    schemaVersion: ONBOARDING_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ONBOARDING_PENDING_TTL_MS,
    payload: value,
  };
  wx.setStorageSync(abilityBaselineStorageKey(owner), envelope);
}

export function loadPendingAbilityBaseline(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingAbilityBaselineCommand | null {
  const owner = required(ownerUserId, "userId");
  const key = abilityBaselineStorageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs), isPendingAbilityBaselineCommand)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingAbilityBaseline(ownerUserId: string): void {
  wx.removeStorageSync(abilityBaselineStorageKey(required(ownerUserId, "userId")));
}

export function storePendingStudyPreference(
  ownerUserId: string,
  value: PendingStudyPreferenceCommand,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  if (!isPendingStudyPreferenceCommand(value)) {
    throw new Error("Refusing to persist an invalid study-preference command");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<PendingStudyPreferenceCommand> = {
    schemaVersion: ONBOARDING_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ONBOARDING_PENDING_TTL_MS,
    payload: value,
  };
  wx.setStorageSync(storageKey(owner), envelope);
}

export function loadPendingStudyPreference(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingStudyPreferenceCommand | null {
  const owner = required(ownerUserId, "userId");
  const key = storageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs), isPendingStudyPreferenceCommand)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingStudyPreference(ownerUserId: string): void {
  wx.removeStorageSync(storageKey(required(ownerUserId, "userId")));
}

export function storePendingResultAcknowledgement(
  ownerUserId: string,
  value: PendingResultAcknowledgement,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  if (!isPendingResultAcknowledgement(value)) {
    throw new Error("Refusing to persist an invalid onboarding-result acknowledgement");
  }
  const createdAtMs = validNow(nowMs);
  const envelope: PendingEnvelope<PendingResultAcknowledgement> = {
    schemaVersion: ONBOARDING_PENDING_SCHEMA_VERSION,
    ownerUserId: owner,
    createdAtMs,
    expiresAtMs: createdAtMs + ONBOARDING_PENDING_TTL_MS,
    payload: value,
  };
  wx.setStorageSync(resultStorageKey(owner), envelope);
}

export function loadPendingResultAcknowledgement(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingResultAcknowledgement | null {
  const owner = required(ownerUserId, "userId");
  const key = resultStorageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (value === undefined || value === null || value === "") return null;
  if (!isEnvelope(value, owner, validNow(nowMs), isPendingResultAcknowledgement)) {
    wx.removeStorageSync(key);
    return null;
  }
  return value.payload;
}

export function clearPendingResultAcknowledgement(ownerUserId: string): void {
  wx.removeStorageSync(resultStorageKey(required(ownerUserId, "userId")));
}

export function clearOnboardingPendingForUser(ownerUserId: string): void {
  clearPendingPreparationExperience(ownerUserId);
  clearPendingQualification(ownerUserId);
  clearPendingPositionPreference(ownerUserId);
  clearPendingAbilityBaseline(ownerUserId);
  clearPendingStudyPreference(ownerUserId);
  clearPendingResultAcknowledgement(ownerUserId);
}

export function clearAllOnboardingPending(): void {
  for (const key of wx.getStorageInfoSync().keys) {
    if (key.startsWith(`${storageRoot}.`)) wx.removeStorageSync(key);
  }
}

function isEnvelope<T>(
  value: unknown,
  ownerUserId: string,
  nowMs: number,
  payloadGuard: (payload: unknown) => payload is T,
): value is PendingEnvelope<T> {
  if (!isRecord(value)) return false;
  return (
    value.schemaVersion === ONBOARDING_PENDING_SCHEMA_VERSION &&
    value.ownerUserId === ownerUserId &&
    Number.isSafeInteger(value.createdAtMs) &&
    Number.isSafeInteger(value.expiresAtMs) &&
    Number(value.createdAtMs) >= 0 &&
    Number(value.expiresAtMs) > Number(value.createdAtMs) &&
    Number(value.expiresAtMs) > nowMs &&
    payloadGuard(value.payload)
  );
}

function isPendingResultAcknowledgement(value: unknown): value is PendingResultAcknowledgement {
  if (!isRecord(value) || !isRecord(value.command)) return false;
  return (
    typeof value.idempotencyKey === "string" &&
    value.idempotencyKey.length >= 8 &&
    Number.isSafeInteger(value.command.expectedProgressVersion) &&
    Number(value.command.expectedProgressVersion) >= 1 &&
    value.command.acknowledgeResult === true &&
    Object.keys(value.command).length === 2
  );
}

function isPendingPreparationExperienceCommand(
  value: unknown,
): value is PendingPreparationExperienceCommand {
  if (!isRecord(value) || !isRecord(value.draft) || !isRecord(value.command)) return false;
  if (typeof value.idempotencyKey !== "string" || value.idempotencyKey.length < 8) return false;
  const rawDraft: unknown = value.draft;
  if (!isPreparationExperienceDraft(rawDraft) || validatePreparationExperience(rawDraft)) {
    return false;
  }
  const command = value.command as unknown as PreparationExperienceWrite;
  try {
    const rebuilt = buildPreparationExperienceCommand(rawDraft, {
      expectedExperienceVersion: command.expectedExperienceVersion,
      expectedProgressVersion: command.expectedProgressVersion,
    });
    return canonicalJson(rebuilt) === canonicalJson(command);
  } catch {
    return false;
  }
}

function isPendingPositionPreferenceCommand(
  value: unknown,
): value is PendingPositionPreferenceCommand {
  if (!isRecord(value) || !isRecord(value.draft) || !isRecord(value.command)) return false;
  if (typeof value.idempotencyKey !== "string" || value.idempotencyKey.length < 8) return false;
  const draft = value.draft as unknown as PositionPreferenceDraft;
  const command = value.command as unknown as PositionPreferenceWrite;
  try {
    const rebuilt = buildPositionPreferenceCommand(draft, command.expectedPreferenceVersion);
    return canonicalJson(rebuilt) === canonicalJson(command);
  } catch {
    return false;
  }
}

function isPendingAbilityBaselineCommand(value: unknown): value is PendingAbilityBaselineCommand {
  if (!isRecord(value) || !isRecord(value.command)) return false;
  if (typeof value.idempotencyKey !== "string" || value.idempotencyKey.length < 8) {
    return false;
  }
  const command = value.command as unknown as AbilityBaselineWrite;
  try {
    const rebuilt = buildAbilityBaselineCommand(
      abilityBaselineDraftFromCommand(command),
      command.expectedBaselineVersion,
    );
    return canonicalJson(rebuilt) === canonicalJson(command);
  } catch {
    return false;
  }
}

function isPendingQualificationCommand(value: unknown): value is PendingQualificationCommand {
  if (!isRecord(value) || !isRecord(value.draft)) return false;
  if (typeof value.idempotencyKey !== "string" || value.idempotencyKey.length < 8) return false;
  const rawDraft: unknown = value.draft;
  if (!isQualificationDraft(rawDraft) || validateOnboardingQualificationDraft(rawDraft)) {
    return false;
  }
  const draft = rawDraft;

  if (value.phase === "profile") {
    if (!Array.isArray(value.facts) || !value.facts.every(isQualificationFactInput)) return false;
    return (
      Object.keys(value).length === 4 &&
      qualificationFactsFingerprint(value.facts) ===
        qualificationFactsFingerprint(qualificationFactsFromDraft(draft))
    );
  }
  if (value.phase !== "milestone" || !isRecord(value.command)) return false;
  return (
    Object.keys(value).length === 4 &&
    Object.keys(value.command).length === 2 &&
    Number.isSafeInteger(value.command.expectedProgressVersion) &&
    Number(value.command.expectedProgressVersion) >= 1 &&
    Number.isSafeInteger(value.command.qualificationProfileVersion) &&
    Number(value.command.qualificationProfileVersion) >= 1
  );
}

function isPendingStudyPreferenceCommand(value: unknown): value is PendingStudyPreferenceCommand {
  if (!isRecord(value) || !isRecord(value.draft) || !isRecord(value.command)) return false;
  if (typeof value.idempotencyKey !== "string" || value.idempotencyKey.length < 8) return false;
  const draft = value.draft as unknown as StudyPreferenceDraft;
  const command = value.command as unknown as StudyPreferenceCommandPayload;
  try {
    const rebuilt = buildStudyPreferenceCommand(draft, {
      expectedPreferenceVersion: command.expectedPreferenceVersion,
      expectedProgressVersion: command.expectedProgressVersion,
      effectiveFrom: command.effectiveFrom,
    });
    return canonicalJson(rebuilt) === canonicalJson(command);
  } catch {
    return false;
  }
}

function storageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}.study-preference`;
}

function preparationExperienceStorageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}.preparation-experience`;
}

function qualificationStorageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}.qualification`;
}

function positionPreferenceStorageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}.position-preference`;
}

function abilityBaselineStorageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}.ability-baseline`;
}

function resultStorageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}.result-acknowledgement`;
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Onboarding pending storage requires ${label}`);
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("Onboarding pending storage requires a valid current timestamp");
  }
  return value;
}

function canonicalJson(value: unknown): string {
  return JSON.stringify(value, Object.keys(value as object).sort());
}

function isQualificationDraft(value: unknown): value is QualificationDraft {
  if (!isRecord(value)) return false;
  return (
    typeof value.education === "string" &&
    typeof value.major === "string" &&
    Number.isSafeInteger(value.graduationYear) &&
    (value.freshGraduate === "" ||
      value.freshGraduate === "true" ||
      value.freshGraduate === "false") &&
    typeof value.politicalStatus === "string" &&
    Number.isSafeInteger(value.workYears) &&
    Number.isSafeInteger(value.grassrootsYears) &&
    typeof value.serviceProject === "string" &&
    typeof value.hukou === "string" &&
    Object.keys(value).length === 9
  );
}

function isPreparationExperienceDraft(value: unknown): value is PreparationExperienceDraft {
  return (
    isRecord(value) &&
    typeof value.firstSystematic === "boolean" &&
    typeof value.externalLearning === "boolean" &&
    typeof value.priorGuokaoExam === "boolean" &&
    typeof value.retake === "boolean" &&
    (value.currentStatus === "parttime" ||
      value.currentStatus === "fulltime" ||
      value.currentStatus === "student") &&
    Object.keys(value).length === 5
  );
}

function isQualificationFactInput(value: unknown): value is QualificationFactInput {
  return (
    isRecord(value) &&
    typeof value.key === "string" &&
    (typeof value.value === "string" ||
      typeof value.value === "number" ||
      typeof value.value === "boolean" ||
      (Array.isArray(value.value) && value.value.every((item) => typeof item === "string"))) &&
    Object.keys(value).length === 2
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
