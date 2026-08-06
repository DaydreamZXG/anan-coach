export const RETAKE_PENDING_TTL_MS = 7 * 24 * 60 * 60 * 1_000;

export interface PendingRetakeProgram {
  ownerUserId: string;
  idempotencyKey: string;
  evidenceId: string;
  createdAtMs: number;
  expiresAtMs: number;
}

export function storePendingRetakeProgram(
  ownerUserId: string,
  input: Pick<PendingRetakeProgram, "idempotencyKey" | "evidenceId">,
  nowMs = Date.now(),
): void {
  const owner = required(ownerUserId);
  const createdAtMs = validNow(nowMs);
  const pending: PendingRetakeProgram = {
    ownerUserId: owner,
    idempotencyKey: required(input.idempotencyKey),
    evidenceId: required(input.evidenceId),
    createdAtMs,
    expiresAtMs: createdAtMs + RETAKE_PENDING_TTL_MS,
  };
  wx.setStorageSync(storageKey(owner), pending);
}

export function loadPendingRetakeProgram(
  ownerUserId: string,
  nowMs = Date.now(),
): PendingRetakeProgram | null {
  const owner = required(ownerUserId);
  const key = storageKey(owner);
  const value: unknown = wx.getStorageSync(key);
  if (!isPending(value, owner, validNow(nowMs))) {
    if (value !== undefined && value !== null && value !== "") wx.removeStorageSync(key);
    return null;
  }
  return value;
}

export function clearPendingRetakeProgram(ownerUserId: string): void {
  wx.removeStorageSync(storageKey(required(ownerUserId)));
}

function isPending(value: unknown, owner: string, nowMs: number): value is PendingRetakeProgram {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Partial<PendingRetakeProgram>;
  return (
    candidate.ownerUserId === owner &&
    typeof candidate.idempotencyKey === "string" &&
    candidate.idempotencyKey.length >= 8 &&
    typeof candidate.evidenceId === "string" &&
    candidate.evidenceId.length > 0 &&
    Number.isSafeInteger(candidate.createdAtMs) &&
    Number.isSafeInteger(candidate.expiresAtMs) &&
    Number(candidate.expiresAtMs) > nowMs
  );
}

function storageKey(ownerUserId: string): string {
  return `gongkao.retake.pending.v1.${encodeURIComponent(ownerUserId)}`;
}

function required(value: string): string {
  const normalized = value.trim();
  if (!normalized) throw new Error("Retake pending storage requires a non-empty value");
  return normalized;
}

function validNow(value: number): number {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error("Retake pending storage requires a valid timestamp");
  }
  return value;
}
