import type { GlobalAskObjectRef } from "../../api/generated/types.gen";

export interface CompanionLaunchContext {
  sourceLabel: string;
  title: string;
  summary: string;
  suggestedQuestion: string;
  routeId: string;
  workspace: "today" | "training" | "position" | "profile";
  objectRefs: GlobalAskObjectRef[];
  taskMode?: "practice" | "retest" | "mock" | "essay";
  taskStage?: "reading" | "answering" | "submitted" | "reviewing";
}

interface StoredCompanionLaunchContext {
  schemaVersion: 1;
  ownerUserId: string;
  createdAt: number;
  context: CompanionLaunchContext;
}

const STORAGE_ROOT = "gongkao.companion.launch-context.v1";
const MAX_AGE_MS = 30 * 60 * 1000;

export function storeCompanionLaunchContext(
  ownerUserId: string,
  context: CompanionLaunchContext,
): void {
  const owner = required(ownerUserId, "userId");
  assertContext(context);
  const value: StoredCompanionLaunchContext = {
    schemaVersion: 1,
    ownerUserId: owner,
    createdAt: Date.now(),
    context: {
      ...context,
      sourceLabel: context.sourceLabel.trim(),
      title: context.title.trim(),
      summary: context.summary.trim(),
      suggestedQuestion: context.suggestedQuestion.trim(),
      routeId: normalizeRouteId(context.routeId),
      objectRefs: context.objectRefs.map((item) => ({ ...item })),
    },
  };
  wx.setStorageSync(storageKey(owner), value);
}

export function consumeCompanionLaunchContext(ownerUserId: string): CompanionLaunchContext | null {
  const owner = required(ownerUserId, "userId");
  const key = storageKey(owner);
  const raw: unknown = wx.getStorageSync(key);
  wx.removeStorageSync(key);
  if (!isStoredContext(raw, owner)) return null;
  if (Date.now() - raw.createdAt > MAX_AGE_MS) return null;
  return raw.context;
}

export function clearCompanionLaunchContexts(): void {
  for (const key of wx.getStorageInfoSync().keys) {
    if (key.startsWith(`${STORAGE_ROOT}.`)) wx.removeStorageSync(key);
  }
}

function isStoredContext(
  value: unknown,
  ownerUserId: string,
): value is StoredCompanionLaunchContext {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<StoredCompanionLaunchContext>;
  if (
    candidate.schemaVersion !== 1 ||
    candidate.ownerUserId !== ownerUserId ||
    typeof candidate.createdAt !== "number" ||
    !candidate.context
  ) {
    return false;
  }
  try {
    assertContext(candidate.context);
    return true;
  } catch {
    return false;
  }
}

function assertContext(context: CompanionLaunchContext): void {
  required(context.sourceLabel, "sourceLabel");
  required(context.title, "title");
  required(context.summary, "summary");
  required(context.suggestedQuestion, "suggestedQuestion");
  const routeId = normalizeRouteId(context.routeId);
  if (!routeId.startsWith("pages/")) throw new Error("Companion context route is invalid");
  if (context.objectRefs.length > 12) throw new Error("Companion context has too many objects");
  const identities = new Set<string>();
  for (const item of context.objectRefs) {
    const identity = `${item.type}:${required(item.id, "objectId")}:${required(item.version, "version")}`;
    if (identities.has(identity)) throw new Error("Companion context objects must be unique");
    identities.add(identity);
  }
  if ((context.taskMode === undefined) !== (context.taskStage === undefined)) {
    throw new Error("Companion task mode and stage must be supplied together");
  }
}

function storageKey(ownerUserId: string): string {
  return `${STORAGE_ROOT}.${encodeURIComponent(ownerUserId)}`;
}

function normalizeRouteId(value: string): string {
  return required(value, "routeId").replace(/^\/+/u, "");
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Companion context requires ${label}`);
  return normalized;
}
