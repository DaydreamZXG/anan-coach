import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stableFingerprint, stableJson } from "../miniprogram/platform/command-id";
import {
  OperationAttemptConflictError,
  OperationAttemptStateError,
  OperationAttemptStore,
  operationAttemptStorageKey,
  operationAttemptStoragePrefix,
  type OperationAttemptState,
} from "../miniprogram/platform/operation-attempt-store";

const nowMs = 1_000;
const expiresAtMs = 10_000;
let storage: Map<string, unknown>;
let entropySequence: number;
let getRandomValues: ReturnType<typeof vi.fn>;

beforeEach(() => {
  storage = new Map<string, unknown>();
  entropySequence = 1;
  getRandomValues = vi.fn(({ length }: { length: number }) => {
    const entropy = new Uint8Array(length);
    entropy[entropy.length - 1] = entropySequence;
    entropySequence += 1;
    return Promise.resolve({ randomValues: entropy.buffer });
  });
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, value),
    removeStorageSync: (key: string) => storage.delete(key),
    getRandomValues,
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("operation attempt canonical input", () => {
  it("stabilizes object keys while preserving arrays, null, and primitives", () => {
    const left = {
      z: null,
      a: ["first", 2, false, { beta: "b", alpha: "a" }],
    };
    const right = {
      a: ["first", 2, false, { alpha: "a", beta: "b" }],
      z: null,
    };

    expect(stableJson(left)).toBe('{"a":["first",2,false,{"alpha":"a","beta":"b"}],"z":null}');
    expect(stableFingerprint(left)).toBe(stableFingerprint(right));
    expect(stableFingerprint({ values: [1, 2] })).not.toBe(stableFingerprint({ values: [2, 1] }));
    expect(stableFingerprint("1")).not.toBe(stableFingerprint(1));
  });

  it("hashes canonical JSON as UTF-8 SHA-256, including Chinese and emoji", () => {
    expect(stableFingerprint("岸岸🦦")).toBe(
      "42ff9fad6e0a23b5b29a19b3c0f656444e8823a2fbfb03e5faf017ea2e39c48f",
    );
    expect(stableFingerprint({ text: "中文😀" })).toBe(
      "fb03fe23ec07afefc1e3ab786b9971782df149d288b1a1288561ea0f360ff2f4",
    );
    expect(stableFingerprint("岸岸🦦")).toMatch(/^[0-9a-f]{64}$/u);
  });

  it("distinguishes an equal-length input pair that collided under the former FNV-1a digest", () => {
    expect(stableFingerprint("0pypok3")).not.toBe(stableFingerprint("1xatgsv"));
  });

  it.each([
    ["root undefined", undefined],
    ["nested undefined", { value: undefined }],
    ["array undefined", [undefined]],
    ["NaN", Number.NaN],
    ["infinity", Number.POSITIVE_INFINITY],
    ["function", { value: () => "unsupported" }],
    ["bigint", 1n],
    ["date", new Date(0)],
  ])("fails closed for %s", (_label, value) => {
    expect(() => stableFingerprint(value)).toThrow();
  });

  it("rejects sparse arrays, cycles, symbols, and array side properties", () => {
    const sparse: unknown[] = [];
    sparse.length = 1;
    const cyclic: { self?: unknown } = {};
    cyclic.self = cyclic;
    const symbolKey = { value: 1 };
    Object.defineProperty(symbolKey, Symbol("hidden"), { value: 2, enumerable: true });
    const extended = [1] as unknown[] & { extra?: string };
    extended.extra = "unsupported";

    for (const value of [sparse, cyclic, symbolKey, extended]) {
      expect(() => stableFingerprint(value)).toThrow();
    }
  });
});

describe("OperationAttemptStore", () => {
  it("creates and reuses one pending attempt for the same owner, scope, and input", async () => {
    const store = new OperationAttemptStore("user-a");
    const first = await begin(store, { answer: "B", elapsedSeconds: 42 });
    const replay = await begin(store, { elapsedSeconds: 42, answer: "B" });

    expect(first).toEqual({
      schemaVersion: 1,
      ownerUserId: "user-a",
      scope: "review:card-1",
      canonicalFingerprint: stableFingerprint({ answer: "B", elapsedSeconds: 42 }),
      idempotencyKey: "review-00000000-0000-4000-8000-000000000001",
      state: "pending",
      createdAtMs: nowMs,
      updatedAtMs: nowMs,
      expiresAtMs,
    });
    expect(replay).toEqual(first);
    expect(getRandomValues).toHaveBeenCalledTimes(1);
  });

  it("serializes concurrent begins across store instances", async () => {
    const firstStore = new OperationAttemptStore("user-a");
    const secondStore = new OperationAttemptStore("user-a");

    const [first, second] = await Promise.all([
      begin(firstStore, { answer: "B" }),
      begin(secondStore, { answer: "B" }),
    ]);

    expect(second).toEqual(first);
    expect(getRandomValues).toHaveBeenCalledTimes(1);
  });

  it("fails closed when unfinished scope input drifts", async () => {
    const store = new OperationAttemptStore("user-a");
    const first = await begin(store, { answer: "B" });

    await expect(begin(store, { answer: "C" })).rejects.toBeInstanceOf(
      OperationAttemptConflictError,
    );
    expect(store.load("review:card-1", nowMs)).toEqual(first);
    expect(getRandomValues).toHaveBeenCalledTimes(1);
  });

  it("keeps the same key after transport outcome becomes unknown", async () => {
    const store = new OperationAttemptStore("user-a");
    const first = await begin(store, { answer: "B" });
    const unknown = store.markTransportUnknown("review:card-1", first.idempotencyKey, nowMs + 1);
    const replay = await begin(store, { answer: "B" }, nowMs + 2);

    expect(unknown).toMatchObject({
      idempotencyKey: first.idempotencyKey,
      state: "unknown",
      updatedAtMs: nowMs + 1,
    });
    expect(replay).toEqual(unknown);
    expect(getRandomValues).toHaveBeenCalledTimes(1);
  });

  it.each<OperationAttemptState>(["succeeded", "terminal", "cancelled"])(
    "does not replay a %s attempt",
    async (completedState) => {
      const store = new OperationAttemptStore("user-a");
      const first = await begin(store, { answer: "B" });
      const completed = transition(store, completedState, first.idempotencyKey, nowMs + 1);
      const next = await begin(store, { answer: "B" }, nowMs + 2);

      expect(completed.state).toBe(completedState);
      expect(next.state).toBe("pending");
      expect(next.idempotencyKey).not.toBe(first.idempotencyKey);
      expect(getRandomValues).toHaveBeenCalledTimes(2);
    },
  );

  it("retains cancellation and rejects stale or post-completion transitions", async () => {
    const store = new OperationAttemptStore("user-a");
    const first = await begin(store, { answer: "B" });

    expect(() => store.cancel("review:card-1", "wrong-key", nowMs + 1)).toThrow(
      OperationAttemptStateError,
    );
    const cancelled = store.cancel("review:card-1", first.idempotencyKey, nowMs + 1);
    expect(store.load("review:card-1", nowMs + 2)).toEqual(cancelled);
    expect(() => store.markSucceeded("review:card-1", first.idempotencyKey, nowMs + 2)).toThrow(
      OperationAttemptStateError,
    );
  });

  it("removes expired records and creates a fresh attempt with caller expiry", async () => {
    const store = new OperationAttemptStore("user-a");
    const first = await begin(store, { answer: "B" });

    expect(store.load("review:card-1", expiresAtMs)).toBeNull();
    const next = await begin(store, { answer: "B" }, expiresAtMs, 20_000);
    expect(next.idempotencyKey).not.toBe(first.idempotencyKey);
    expect(next.expiresAtMs).toBe(20_000);
    await expect(begin(store, { answer: "B" }, 20_000, 20_000)).rejects.toThrow(
      "expiry must be in the future",
    );
  });

  it("clears malformed and wrong-owner records instead of accepting them", async () => {
    const key = operationAttemptStorageKey("user-a", "review:card-1");
    storage.set(key, {
      schemaVersion: 1,
      ownerUserId: "user-b",
      scope: "review:card-1",
      state: "pending",
    });
    const store = new OperationAttemptStore("user-a");

    expect(store.load("review:card-1", nowMs)).toBeNull();
    expect(storage.has(key)).toBe(false);
    const fresh = await begin(store, { answer: "B" });
    expect(fresh.ownerUserId).toBe("user-a");
  });

  it("isolates accounts and encodes owner and scope without segment collisions", async () => {
    const userA = new OperationAttemptStore("user.a");
    const userB = new OperationAttemptStore("user");
    const first = await begin(userA, { answer: "B" }, nowMs, expiresAtMs, "review.card/1");

    expect(userB.load("a.review.card/1", nowMs)).toBeNull();
    expect(operationAttemptStorageKey("user.a", "review.card/1")).toBe(
      `${operationAttemptStoragePrefix()}.user%2Ea.review%2Ecard%2F1`,
    );
    expect(operationAttemptStorageKey("user.a", "review.card/1")).not.toBe(
      operationAttemptStorageKey("user", "a.review.card/1"),
    );
    expect(storage.get(operationAttemptStorageKey("user.a", "review.card/1"))).toEqual(first);
  });

  it("rejects blank or whitespace-ambiguous owner and scope identities", async () => {
    for (const ownerUserId of ["", " ", " user-a", "user-a ", "\tuser-a"]) {
      expect(() => new OperationAttemptStore(ownerUserId)).toThrow();
    }

    const store = new OperationAttemptStore("user-a");
    for (const scope of ["", " ", " review:card-1", "review:card-1 ", "\nreview:card-1"]) {
      await expect(begin(store, { answer: "B" }, nowMs, expiresAtMs, scope)).rejects.toThrow();
      expect(() => operationAttemptStorageKey("user-a", scope)).toThrow();
    }
    expect(getRandomValues).not.toHaveBeenCalled();
  });
});

function begin(
  store: OperationAttemptStore,
  canonicalInput: unknown,
  currentTimeMs: number = nowMs,
  expiryMs: number = expiresAtMs,
  scope: string = "review:card-1",
) {
  return store.begin({
    scope,
    canonicalInput,
    idempotencyKeyPrefix: "review",
    expiresAtMs: expiryMs,
    nowMs: currentTimeMs,
  });
}

function transition(
  store: OperationAttemptStore,
  state: OperationAttemptState,
  idempotencyKey: string,
  updatedAtMs: number,
) {
  switch (state) {
    case "succeeded":
      return store.markSucceeded("review:card-1", idempotencyKey, updatedAtMs);
    case "terminal":
      return store.markTerminal("review:card-1", idempotencyKey, updatedAtMs);
    case "cancelled":
      return store.cancel("review:card-1", idempotencyKey, updatedAtMs);
    case "pending":
    case "unknown":
      throw new Error(`unsupported completed test state: ${state}`);
  }
}
