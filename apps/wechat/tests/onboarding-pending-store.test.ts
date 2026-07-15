import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  clearAllOnboardingPending,
  clearOnboardingPendingForUser,
  loadPendingQualification,
  loadPendingResultAcknowledgement,
  loadPendingStudyPreference,
  ONBOARDING_PENDING_TTL_MS,
  storePendingResultAcknowledgement,
  storePendingQualification,
  storePendingStudyPreference,
  type PendingStudyPreferenceCommand,
} from "../miniprogram/features/onboarding/pending-store";
import {
  buildStudyPreferenceCommand,
  defaultStudyPreferenceDraft,
} from "../miniprogram/features/onboarding/study-preference";

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", storageWx(storage));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("onboarding pending storage", () => {
  it("keeps an unknown-outcome command byte-stable and scoped to one account", () => {
    const pending = command();
    storePendingStudyPreference("user-a", pending, 1_000);

    expect(loadPendingStudyPreference("user-a", 2_000)).toEqual(pending);
    expect(loadPendingStudyPreference("user-b", 2_000)).toBeNull();
  });

  it("deletes expired or structurally changed commands instead of replaying them", () => {
    const pending = command();
    storePendingStudyPreference("user-a", pending, 1_000);
    expect(loadPendingStudyPreference("user-a", 1_000 + ONBOARDING_PENDING_TTL_MS)).toBeNull();

    storePendingStudyPreference("user-a", pending, 2_000);
    const [key] = storage.keys();
    const stored = storage.get(key ?? "") as { payload: PendingStudyPreferenceCommand };
    stored.payload.command.weekdayMinutes = 999;
    expect(loadPendingStudyPreference("user-a", 3_000)).toBeNull();
  });

  it("keeps qualification recovery exact and scoped to its authenticated account", () => {
    const draft = {
      education: "本科",
      major: "法学",
      freshGraduate: "true" as const,
      politicalStatus: "群众",
      grassrootsYears: 0,
      hukou: "",
    };
    storePendingQualification(
      "user-a",
      {
        phase: "profile",
        idempotencyKey: "qualification-profile-command-1",
        draft,
        facts: [
          { key: "education", value: "本科" },
          { key: "major", value: "法学" },
          { key: "fresh_graduate", value: true },
          { key: "political_status", value: "群众" },
          { key: "grassroots_years", value: 0 },
        ],
      },
      1_000,
    );

    expect(loadPendingQualification("user-a", 2_000)).toMatchObject({
      phase: "profile",
      idempotencyKey: "qualification-profile-command-1",
    });
    expect(loadPendingQualification("user-b", 2_000)).toBeNull();

    const key = [...storage.keys()].find((item) => item.endsWith(".qualification"));
    if (!key) throw new Error("qualification recovery key was not stored");
    const stored = storage.get(key) as {
      payload: { facts: Array<{ key: string; value: unknown }> };
    };
    stored.payload.facts[0] = { key: "education", value: "博士" };
    expect(loadPendingQualification("user-a", 2_000)).toBeNull();
  });

  it("cleans only the intended account unless identity is no longer trustworthy", () => {
    const pending = command();
    storePendingStudyPreference("user-a", pending, 1_000);
    storePendingStudyPreference("user-b", pending, 1_000);
    storage.set("unrelated", true);

    clearOnboardingPendingForUser("user-a");
    expect(loadPendingStudyPreference("user-a", 2_000)).toBeNull();
    expect(loadPendingStudyPreference("user-b", 2_000)).toEqual(pending);

    clearAllOnboardingPending();
    expect(loadPendingStudyPreference("user-b", 2_000)).toBeNull();
    expect(storage.get("unrelated")).toBe(true);
  });

  it("keeps result acknowledgement recovery account-scoped and exact", () => {
    const pending = {
      idempotencyKey: "onboarding-result-command-1",
      command: { expectedProgressVersion: 3, acknowledgeResult: true as const },
    };
    storePendingResultAcknowledgement("user-a", pending, 1_000);

    expect(loadPendingResultAcknowledgement("user-a", 2_000)).toEqual(pending);
    expect(loadPendingResultAcknowledgement("user-b", 2_000)).toBeNull();
    clearOnboardingPendingForUser("user-a");
    expect(loadPendingResultAcknowledgement("user-a", 2_000)).toBeNull();
  });
});

function command(): PendingStudyPreferenceCommand {
  return {
    idempotencyKey: "onboarding-study-preference-command-1",
    draft: { ...defaultStudyPreferenceDraft },
    command: buildStudyPreferenceCommand(defaultStudyPreferenceDraft, {
      expectedPreferenceVersion: 0,
      expectedProgressVersion: 2,
      effectiveFrom: "2026-07-13",
    }),
  };
}

function storageWx(values: Map<string, unknown>) {
  return {
    getStorageSync: (key: string) => values.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => {
      values.set(key, clone(value));
    },
    removeStorageSync: (key: string) => {
      values.delete(key);
    },
    getStorageInfoSync: () => ({
      keys: [...values.keys()],
      currentSize: 0,
      limitSize: 10_240,
    }),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
