export interface CoachEvidenceCounters {
  scoreEvidenceCount: number;
  xingceAttemptCount: number;
  xingceMasteryCount: number;
  essayMasteryCount: number;
  taskExecutionCount: number;
  externalAnswerCount: number;
}

export interface CoachErrorPatternSnapshot {
  patternId: string;
  displayName: string;
  status: "confirmed" | "counterevidence" | "retired";
  counterevidenceCount: number;
}

export interface CoachTaskBeforeSnapshot {
  version: 1;
  ownerUserId: string;
  localDate: string;
  capturedAt: string;
  taskId: string;
  taskTitle: string;
  taskKind: "practice" | "reflection" | "verification" | "mock_exam" | "essay";
  taskReason: string;
  completionRule: string;
  planVersionId: string;
  profileVersionId?: string;
  evidence?: CoachEvidenceCounters;
  errorPatterns?: CoachErrorPatternSnapshot[];
}

const storageRoot = "gongkao.coach-loop.before-task.v1";

export function storeCoachTaskBeforeSnapshot(snapshot: CoachTaskBeforeSnapshot): void {
  const normalized = validateSnapshot(snapshot);
  wx.setStorageSync(storageKey(normalized.ownerUserId), normalized);
}

export function readCoachTaskBeforeSnapshot(
  ownerUserId: string,
  now = new Date(),
): CoachTaskBeforeSnapshot | null {
  const owner = required(ownerUserId, "userId");
  const key = storageKey(owner);
  const raw: unknown = wx.getStorageSync(key);
  if (!isCoachTaskBeforeSnapshot(raw) || raw.ownerUserId !== owner) {
    if (raw !== undefined && raw !== null && raw !== "") wx.removeStorageSync(key);
    return null;
  }
  if (raw.localDate !== localDate(now)) {
    wx.removeStorageSync(key);
    return null;
  }
  return raw;
}

export function clearCoachTaskBeforeSnapshot(ownerUserId: string): void {
  wx.removeStorageSync(storageKey(required(ownerUserId, "userId")));
}

export function coachLocalDate(now = new Date()): string {
  return localDate(now);
}

function validateSnapshot(snapshot: CoachTaskBeforeSnapshot): CoachTaskBeforeSnapshot {
  if (!isCoachTaskBeforeSnapshot(snapshot)) {
    throw new Error("Coach task snapshot is invalid");
  }
  return snapshot;
}

function isCoachTaskBeforeSnapshot(value: unknown): value is CoachTaskBeforeSnapshot {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<CoachTaskBeforeSnapshot>;
  return (
    candidate.version === 1 &&
    nonEmpty(candidate.ownerUserId) &&
    /^\d{4}-\d{2}-\d{2}$/u.test(candidate.localDate ?? "") &&
    validDate(candidate.capturedAt) &&
    nonEmpty(candidate.taskId) &&
    nonEmpty(candidate.taskTitle) &&
    isTaskKind(candidate.taskKind) &&
    typeof candidate.taskReason === "string" &&
    typeof candidate.completionRule === "string" &&
    nonEmpty(candidate.planVersionId) &&
    (candidate.profileVersionId === undefined || nonEmpty(candidate.profileVersionId)) &&
    (candidate.evidence === undefined || isEvidenceCounters(candidate.evidence)) &&
    (candidate.errorPatterns === undefined || isErrorPatterns(candidate.errorPatterns))
  );
}

function isErrorPatterns(value: unknown): value is CoachErrorPatternSnapshot[] {
  return (
    Array.isArray(value) &&
    value.length <= 100 &&
    value.every((item) => {
      if (!item || typeof item !== "object") return false;
      const pattern = item as Partial<CoachErrorPatternSnapshot>;
      return (
        nonEmpty(pattern.patternId) &&
        nonEmpty(pattern.displayName) &&
        (pattern.status === "confirmed" ||
          pattern.status === "counterevidence" ||
          pattern.status === "retired") &&
        Number.isInteger(pattern.counterevidenceCount) &&
        Number(pattern.counterevidenceCount) >= 0
      );
    })
  );
}

function isTaskKind(value: unknown): value is CoachTaskBeforeSnapshot["taskKind"] {
  return (
    value === "practice" ||
    value === "reflection" ||
    value === "verification" ||
    value === "mock_exam" ||
    value === "essay"
  );
}

function isEvidenceCounters(value: unknown): value is CoachEvidenceCounters {
  if (!value || typeof value !== "object") return false;
  const counters = value as Partial<CoachEvidenceCounters>;
  return [
    counters.scoreEvidenceCount,
    counters.xingceAttemptCount,
    counters.xingceMasteryCount,
    counters.essayMasteryCount,
    counters.taskExecutionCount,
    counters.externalAnswerCount,
  ].every((item) => Number.isInteger(item) && Number(item) >= 0);
}

function localDate(now: Date): string {
  return [
    String(now.getFullYear()),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

function storageKey(ownerUserId: string): string {
  return `${storageRoot}.${encodeURIComponent(ownerUserId)}`;
}

function validDate(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function nonEmpty(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error(`Coach task snapshot requires ${label}`);
  return normalized;
}
