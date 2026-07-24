import { isEssayDraftingDraft, type EssayDraftingDraft } from "./drafting";

const schemaVersion = 1 as const;
const storageRoot = `gongkao.essay.drafting-draft.v${String(schemaVersion)}`;
const ttlMilliseconds = 7 * 24 * 60 * 60 * 1_000;

interface StoredEssayDraftingDraft {
  schemaVersion: typeof schemaVersion;
  ownerUserId: string;
  taskVersionId: string;
  planTaskId: string;
  updatedAtMs: number;
  expiresAtMs: number;
  draft: EssayDraftingDraft;
}

interface EssayDraftingDraftContext {
  taskVersionId: string;
  planTaskId: string;
}

export function storeEssayDraftingDraft(
  ownerUserId: string,
  context: EssayDraftingDraftContext,
  draft: EssayDraftingDraft,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId, "userId");
  const taskVersionId = required(context.taskVersionId, "taskVersionId");
  const planTaskId = required(context.planTaskId, "planTaskId");
  if (!isEssayDraftingDraft(draft)) {
    throw new Error("Refusing to persist an invalid essay drafting draft");
  }
  const updatedAtMs = validNow(nowMs);
  const stored: StoredEssayDraftingDraft = {
    schemaVersion,
    ownerUserId: owner,
    taskVersionId,
    planTaskId,
    updatedAtMs,
    expiresAtMs: updatedAtMs + ttlMilliseconds,
    draft: copyDraft(draft),
  };
  wx.setStorageSync(key(owner, planTaskId), stored);
}

export function loadEssayDraftingDraft(
  ownerUserId: string,
  context: EssayDraftingDraftContext,
  nowMs = Date.now(),
): EssayDraftingDraft | null {
  const owner = required(ownerUserId, "userId");
  const taskVersionId = required(context.taskVersionId, "taskVersionId");
  const planTaskId = required(context.planTaskId, "planTaskId");
  const storageKey = key(owner, planTaskId);
  const value: unknown = wx.getStorageSync(storageKey);
  if (value === "" || value === undefined || value === null) return null;
  if (
    !isStoredDraft(value, {
      ownerUserId: owner,
      taskVersionId,
      planTaskId,
      nowMs: validNow(nowMs),
    })
  ) {
    wx.removeStorageSync(storageKey);
    return null;
  }
  return copyDraft(value.draft);
}

export function clearEssayDraftingDraft(ownerUserId: string, planTaskId: string): void {
  wx.removeStorageSync(key(ownerUserId, planTaskId));
}

export function clearEssayDraftingDraftsForUser(ownerUserId: string): void {
  const accountPrefix = `${storageRoot}.${encodeKeyPart(required(ownerUserId, "userId"))}.`;
  for (const storageKey of wx.getStorageInfoSync().keys) {
    if (storageKey.startsWith(accountPrefix)) wx.removeStorageSync(storageKey);
  }
}

export function clearAllEssayDraftingDrafts(): void {
  for (const storageKey of wx.getStorageInfoSync().keys) {
    if (storageKey.startsWith(`${storageRoot}.`)) wx.removeStorageSync(storageKey);
  }
}

function isStoredDraft(
  value: unknown,
  context: {
    ownerUserId: string;
    taskVersionId: string;
    planTaskId: string;
    nowMs: number;
  },
): value is StoredEssayDraftingDraft {
  return (
    isRecord(value) &&
    value.schemaVersion === schemaVersion &&
    value.ownerUserId === context.ownerUserId &&
    value.taskVersionId === context.taskVersionId &&
    value.planTaskId === context.planTaskId &&
    Number.isSafeInteger(value.updatedAtMs) &&
    Number.isSafeInteger(value.expiresAtMs) &&
    Number(value.updatedAtMs) >= 0 &&
    Number(value.expiresAtMs) > Number(value.updatedAtMs) &&
    Number(value.expiresAtMs) > context.nowMs &&
    isEssayDraftingDraft(value.draft)
  );
}

function copyDraft(draft: EssayDraftingDraft): EssayDraftingDraft {
  return {
    ...draft,
    reflectionCauseCodes: [...draft.reflectionCauseCodes],
  };
}

function key(ownerUserId: string, planTaskId: string): string {
  return `${storageRoot}.${encodeKeyPart(required(ownerUserId, "userId"))}.${encodeKeyPart(required(planTaskId, "planTaskId"))}`;
}

function encodeKeyPart(value: string): string {
  return encodeURIComponent(value);
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Essay drafting storage requires ${label}`);
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("Essay drafting storage requires a valid current timestamp");
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
