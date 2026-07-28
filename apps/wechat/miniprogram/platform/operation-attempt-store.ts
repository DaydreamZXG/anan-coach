import { createIdempotencyKey, stableFingerprint } from "./command-id";

const storagePrefix = "gongkao.operation-attempt.v1";
const schemaVersion = 1;
const reusableStates = new Set<OperationAttemptState>(["pending", "unknown"]);
const completedStates = new Set<OperationAttemptState>(["succeeded", "terminal", "cancelled"]);
const pendingBegins = new Map<string, Promise<OperationAttemptRecord>>();

export type OperationAttemptState = "pending" | "unknown" | "succeeded" | "terminal" | "cancelled";

export interface OperationAttemptRecord {
  schemaVersion: 1;
  ownerUserId: string;
  scope: string;
  canonicalFingerprint: string;
  idempotencyKey: string;
  state: OperationAttemptState;
  createdAtMs: number;
  updatedAtMs: number;
  expiresAtMs: number;
}

export interface BeginOperationAttemptInput {
  scope: string;
  canonicalInput: unknown;
  idempotencyKeyPrefix: string;
  expiresAtMs: number;
  nowMs?: number;
}

export class OperationAttemptConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OperationAttemptConflictError";
  }
}

export class OperationAttemptStateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OperationAttemptStateError";
  }
}

export class OperationAttemptStore {
  constructor(private readonly ownerUserId: string) {
    assertIdentityPart(ownerUserId, "owner user ID");
  }

  async begin(input: BeginOperationAttemptInput): Promise<OperationAttemptRecord> {
    assertIdentityPart(input.scope, "operation scope");
    const key = operationAttemptStorageKey(this.ownerUserId, input.scope);
    const pending = pendingBegins.get(key);
    if (pending) {
      await pending;
      return this.begin(input);
    }

    const started = this.beginUnlocked(input);
    pendingBegins.set(key, started);
    try {
      return await started;
    } finally {
      if (pendingBegins.get(key) === started) {
        pendingBegins.delete(key);
      }
    }
  }

  private async beginUnlocked(input: BeginOperationAttemptInput): Promise<OperationAttemptRecord> {
    const nowMs = input.nowMs ?? Date.now();
    assertTimestamp(nowMs, "current time");
    assertTimestamp(input.expiresAtMs, "attempt expiry");
    if (input.expiresAtMs <= nowMs) {
      throw new Error("operation attempt expiry must be in the future");
    }

    const canonicalFingerprint = stableFingerprint(input.canonicalInput);
    const existing = this.load(input.scope, nowMs);
    if (existing && reusableStates.has(existing.state)) {
      if (existing.canonicalFingerprint !== canonicalFingerprint) {
        throw new OperationAttemptConflictError(
          "operation scope has an unfinished attempt with different canonical input",
        );
      }
      return existing;
    }

    const idempotencyKey = await createIdempotencyKey(input.idempotencyKeyPrefix);
    const record: OperationAttemptRecord = {
      schemaVersion,
      ownerUserId: this.ownerUserId,
      scope: input.scope,
      canonicalFingerprint,
      idempotencyKey,
      state: "pending",
      createdAtMs: nowMs,
      updatedAtMs: nowMs,
      expiresAtMs: input.expiresAtMs,
    };
    wx.setStorageSync(operationAttemptStorageKey(this.ownerUserId, input.scope), record);
    return copyRecord(record);
  }

  load(scope: string, nowMs: number = Date.now()): OperationAttemptRecord | null {
    assertIdentityPart(scope, "operation scope");
    assertTimestamp(nowMs, "current time");
    const key = operationAttemptStorageKey(this.ownerUserId, scope);
    const value: unknown = wx.getStorageSync(key);
    if (isMissingStorageValue(value)) return null;
    if (!isOperationAttemptRecord(value, this.ownerUserId, scope) || value.expiresAtMs <= nowMs) {
      wx.removeStorageSync(key);
      return null;
    }
    return copyRecord(value);
  }

  markTransportUnknown(
    scope: string,
    idempotencyKey: string,
    updatedAtMs: number = Date.now(),
  ): OperationAttemptRecord {
    return this.transition(scope, idempotencyKey, "unknown", updatedAtMs);
  }

  markSucceeded(
    scope: string,
    idempotencyKey: string,
    updatedAtMs: number = Date.now(),
  ): OperationAttemptRecord {
    return this.transition(scope, idempotencyKey, "succeeded", updatedAtMs);
  }

  markTerminal(
    scope: string,
    idempotencyKey: string,
    updatedAtMs: number = Date.now(),
  ): OperationAttemptRecord {
    return this.transition(scope, idempotencyKey, "terminal", updatedAtMs);
  }

  cancel(
    scope: string,
    idempotencyKey: string,
    updatedAtMs: number = Date.now(),
  ): OperationAttemptRecord {
    return this.transition(scope, idempotencyKey, "cancelled", updatedAtMs);
  }

  private transition(
    scope: string,
    idempotencyKey: string,
    nextState: OperationAttemptState,
    updatedAtMs: number,
  ): OperationAttemptRecord {
    assertTimestamp(updatedAtMs, "attempt update time");
    const current = this.load(scope, updatedAtMs);
    if (!current) {
      throw new OperationAttemptStateError("operation attempt is missing, expired, or malformed");
    }
    if (current.idempotencyKey !== idempotencyKey) {
      throw new OperationAttemptStateError("operation attempt key does not own the stored scope");
    }
    if (updatedAtMs < current.updatedAtMs) {
      throw new OperationAttemptStateError("operation attempt update time cannot move backwards");
    }
    if (completedStates.has(current.state)) {
      if (current.state === nextState) return current;
      throw new OperationAttemptStateError("completed operation attempt cannot change state");
    }
    if (nextState === "pending") {
      throw new OperationAttemptStateError("operation attempt cannot transition back to pending");
    }

    const updated: OperationAttemptRecord = {
      ...current,
      state: nextState,
      updatedAtMs,
    };
    wx.setStorageSync(operationAttemptStorageKey(this.ownerUserId, scope), updated);
    return copyRecord(updated);
  }
}

export function operationAttemptStorageKey(ownerUserId: string, scope: string): string {
  assertIdentityPart(ownerUserId, "owner user ID");
  assertIdentityPart(scope, "operation scope");
  return `${storagePrefix}.${encodeStorageSegment(ownerUserId)}.${encodeStorageSegment(scope)}`;
}

export function operationAttemptStoragePrefix(): string {
  return storagePrefix;
}

function isOperationAttemptRecord(
  value: unknown,
  expectedOwnerUserId: string,
  expectedScope: string,
): value is OperationAttemptRecord {
  if (!isPlainObject(value)) return false;
  const keys = Object.keys(value);
  const expectedKeys = [
    "canonicalFingerprint",
    "createdAtMs",
    "expiresAtMs",
    "idempotencyKey",
    "ownerUserId",
    "schemaVersion",
    "scope",
    "state",
    "updatedAtMs",
  ];
  if (
    keys.length !== expectedKeys.length ||
    expectedKeys.some((key) => !Object.prototype.hasOwnProperty.call(value, key))
  ) {
    return false;
  }

  const state = value.state;
  return (
    value.schemaVersion === schemaVersion &&
    value.ownerUserId === expectedOwnerUserId &&
    value.scope === expectedScope &&
    typeof value.canonicalFingerprint === "string" &&
    value.canonicalFingerprint.length > 0 &&
    typeof value.idempotencyKey === "string" &&
    value.idempotencyKey.length > 0 &&
    isOperationAttemptState(state) &&
    isTimestamp(value.createdAtMs) &&
    isTimestamp(value.updatedAtMs) &&
    isTimestamp(value.expiresAtMs) &&
    value.createdAtMs <= value.updatedAtMs &&
    value.updatedAtMs < value.expiresAtMs
  );
}

function isOperationAttemptState(value: unknown): value is OperationAttemptState {
  return (
    value === "pending" ||
    value === "unknown" ||
    value === "succeeded" ||
    value === "terminal" ||
    value === "cancelled"
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype: unknown = Object.getPrototypeOf(value) as unknown;
  return prototype === Object.prototype || prototype === null;
}

function isTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function assertTimestamp(value: number, label: string): void {
  if (!isTimestamp(value)) {
    throw new Error(`${label} must be a non-negative safe integer`);
  }
}

function assertIdentityPart(value: string, label: string): void {
  if (value.trim().length === 0) {
    throw new Error(`${label} must not be empty`);
  }
  if (value !== value.trim()) {
    throw new Error(`${label} must not contain leading or trailing whitespace`);
  }
}

function encodeStorageSegment(value: string): string {
  return encodeURIComponent(value).replace(/\./gu, "%2E");
}

function isMissingStorageValue(value: unknown): boolean {
  return value === undefined || value === null || value === "";
}

function copyRecord(record: OperationAttemptRecord): OperationAttemptRecord {
  return { ...record };
}
