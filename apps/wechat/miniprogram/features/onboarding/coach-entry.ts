export type CoachRecognitionPath =
  "score_report" | "wrong_question" | "quick_diagnostic" | "position_first";

export type CoachEntryEvidenceSource = "score_report" | "external_answer" | "position_preference";

export interface CoachEntryIntent {
  version: 1;
  ownerUserId: string;
  path: CoachRecognitionPath;
  selectedAt: string;
  evidenceSource?: CoachEntryEvidenceSource;
  evidenceCapturedAt?: string;
}

const storageRoot = "gongkao.coach-entry.v1";

export function storeCoachEntryIntent(
  ownerUserId: string,
  path: CoachRecognitionPath,
  now = new Date(),
): CoachEntryIntent {
  const owner = required(ownerUserId, "userId");
  const value: CoachEntryIntent = {
    version: 1,
    ownerUserId: owner,
    path,
    selectedAt: now.toISOString(),
  };
  wx.setStorageSync(storageKey(owner), value);
  return value;
}

export function readCoachEntryIntent(ownerUserId: string): CoachEntryIntent | null {
  const owner = required(ownerUserId, "userId");
  const key = storageKey(owner);
  const raw: unknown = wx.getStorageSync(key);
  if (!isCoachEntryIntent(raw) || raw.ownerUserId !== owner) {
    if (raw !== undefined && raw !== null && raw !== "") wx.removeStorageSync(key);
    return null;
  }
  return raw;
}

export function markCoachEntryEvidence(
  ownerUserId: string,
  evidenceSource: CoachEntryEvidenceSource,
  now = new Date(),
): CoachEntryIntent | null {
  const current = readCoachEntryIntent(ownerUserId);
  if (!current) return null;
  const updated: CoachEntryIntent = {
    ...current,
    evidenceSource,
    evidenceCapturedAt: now.toISOString(),
  };
  wx.setStorageSync(storageKey(current.ownerUserId), updated);
  return updated;
}

export function clearCoachEntryIntent(ownerUserId: string): void {
  wx.removeStorageSync(storageKey(required(ownerUserId, "userId")));
}

function storageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}`;
}

function isCoachEntryIntent(value: unknown): value is CoachEntryIntent {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<CoachEntryIntent>;
  return (
    candidate.version === 1 &&
    typeof candidate.ownerUserId === "string" &&
    isRecognitionPath(candidate.path) &&
    validDate(candidate.selectedAt) &&
    (candidate.evidenceSource === undefined || isEvidenceSource(candidate.evidenceSource)) &&
    (candidate.evidenceCapturedAt === undefined || validDate(candidate.evidenceCapturedAt))
  );
}

function isEvidenceSource(value: unknown): value is CoachEntryEvidenceSource {
  return value === "score_report" || value === "external_answer" || value === "position_preference";
}

function isRecognitionPath(value: unknown): value is CoachRecognitionPath {
  return (
    value === "score_report" ||
    value === "wrong_question" ||
    value === "quick_diagnostic" ||
    value === "position_first"
  );
}

function validDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Coach entry requires ${label}`);
  return normalized;
}
