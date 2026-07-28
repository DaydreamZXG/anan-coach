import { createIdempotencyKey, stableFingerprint, stableJson } from "./command-id";

// v1 did not persist canonicalInput, so it cannot be migrated or replayed safely.
// A separate v2 prefix leaves v1 records intact for rollback; unresolved v1 attempts
// block creation of a new v2 key until their outcome is recovered or they expire.
const storagePrefix = "gongkao.operation-attempt.v2";
const legacyStoragePrefix = "gongkao.operation-attempt.v1";
const schemaVersion = 2;
const reusableStates = new Set<OperationAttemptState>(["pending", "unknown"]);
const completedStates = new Set<OperationAttemptState>(["succeeded", "terminal", "cancelled"]);
const pendingBegins = new Map<string, Promise<OperationAttemptRecord>>();

export type OperationAttemptState = "pending" | "unknown" | "succeeded" | "terminal" | "cancelled";

export interface OperationAttemptRecord {
  schemaVersion: 2;
  ownerUserId: string;
  scope: string;
  canonicalInput: unknown;
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

interface LegacyOperationAttemptRecord {
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

export class OperationAttemptConflictError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OperationAttemptConflictError";
  }
}

export class OperationAttemptMigrationRequiredError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "OperationAttemptMigrationRequiredError";
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

    const canonicalInput = copyCanonicalInput(input.canonicalInput);
    const canonicalFingerprint = stableFingerprint(canonicalInput);
    const existing = this.load(input.scope, nowMs);
    if (existing && reusableStates.has(existing.state)) {
      if (existing.canonicalFingerprint !== canonicalFingerprint) {
        throw new OperationAttemptConflictError(
          "operation scope has an unfinished attempt with different canonical input",
        );
      }
      return existing;
    }
    if (this.hasUnresolvedLegacyAttempt(input.scope, nowMs)) {
      throw new OperationAttemptMigrationRequiredError(
        "legacy operation attempt requires recovery before a new attempt can begin",
      );
    }

    const idempotencyKey = await createIdempotencyKey(input.idempotencyKeyPrefix);
    const record: OperationAttemptRecord = {
      schemaVersion,
      ownerUserId: this.ownerUserId,
      scope: input.scope,
      canonicalInput,
      canonicalFingerprint,
      idempotencyKey,
      state: "pending",
      createdAtMs: nowMs,
      updatedAtMs: nowMs,
      expiresAtMs: input.expiresAtMs,
    };
    wx.setStorageSync(
      operationAttemptStorageKey(this.ownerUserId, input.scope),
      copyRecord(record),
    );
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

  private hasUnresolvedLegacyAttempt(scope: string, nowMs: number): boolean {
    const value: unknown = wx.getStorageSync(
      operationAttemptStorageKeyForPrefix(legacyStoragePrefix, this.ownerUserId, scope),
    );
    return (
      isLegacyOperationAttemptRecord(value, this.ownerUserId, scope) &&
      reusableStates.has(value.state) &&
      value.expiresAtMs > nowMs
    );
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
    wx.setStorageSync(operationAttemptStorageKey(this.ownerUserId, scope), copyRecord(updated));
    return copyRecord(updated);
  }
}

export function operationAttemptStorageKey(ownerUserId: string, scope: string): string {
  return operationAttemptStorageKeyForPrefix(storagePrefix, ownerUserId, scope);
}

function operationAttemptStorageKeyForPrefix(
  prefix: string,
  ownerUserId: string,
  scope: string,
): string {
  assertIdentityPart(ownerUserId, "owner user ID");
  assertIdentityPart(scope, "operation scope");
  return `${prefix}.${encodeStorageSegment(ownerUserId)}.${encodeStorageSegment(scope)}`;
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
    "canonicalInput",
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
  if (
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
  ) {
    return canonicalInputMatchesFingerprint(value.canonicalInput, value.canonicalFingerprint);
  }
  return false;
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

function isLegacyOperationAttemptRecord(
  value: unknown,
  expectedOwnerUserId: string,
  expectedScope: string,
): value is LegacyOperationAttemptRecord {
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
    value.schemaVersion === 1 &&
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
  return {
    ...record,
    canonicalInput: copyCanonicalInput(record.canonicalInput),
  };
}

function copyCanonicalInput(value: unknown): unknown {
  return JSON.parse(stableJson(value)) as unknown;
}

function canonicalInputMatchesFingerprint(
  canonicalInput: unknown,
  canonicalFingerprint: string,
): boolean {
  try {
    return stableFingerprint(canonicalInput) === canonicalFingerprint;
  } catch {
    return false;
  }
}
