import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../miniprogram/api/client";
import type {
  OnboardingProgress,
  StudyPreferenceResult,
} from "../miniprogram/api/generated/types.gen";
import type { OnboardingApi } from "../miniprogram/api/onboarding";
import {
  StudyPreferenceController,
  type StudyPreferenceControllerState,
} from "../miniprogram/features/onboarding/study-preference-controller";
import { loadPendingStudyPreference } from "../miniprogram/features/onboarding/pending-store";

type StudyApi = Pick<OnboardingApi, "getProgress" | "getStudyPreference" | "putStudyPreference">;

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({ keys: [...storage.keys()], currentSize: 0, limitSize: 10_240 }),
  });
});

describe("study preference controller", () => {
  it("writes the versioned weekly rhythm and advances only from the server response", async () => {
    const putStudyPreference = vi.fn(() => Promise.resolve(envelope(studyResult())));
    const advance = vi.fn();
    let current: StudyPreferenceControllerState | undefined;
    const controller = new StudyPreferenceController({
      userId: "user-a",
      api: api({ putStudyPreference }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: advance,
      createCommandKey: () => Promise.resolve("onboarding-preference-command-1"),
      today: () => "2026-07-13",
    });

    await controller.load();
    controller.adjustMinutes("weekdayMinutes", 15);
    await controller.submit();

    expect(putStudyPreference).toHaveBeenCalledWith(
      expect.objectContaining({
        expectedProgressVersion: 2,
        expectedPreferenceVersion: 0,
        weekdayMinutes: 60,
        weekendMinutes: 120,
        fixedRestDay: "sunday",
        mockExamDay: "saturday",
      }),
      "onboarding-preference-command-1",
    );
    expect(current?.pendingRecovery).toBe(false);
    expect(loadPendingStudyPreference("user-a")).toBeNull();
    expect(advance).toHaveBeenCalledWith("result");
  });

  it("keeps and replays the exact command after an uncertain network result", async () => {
    const accepted = studyResult();
    const putStudyPreference = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-1"))
      .mockResolvedValueOnce(envelope(accepted));
    let current: StudyPreferenceControllerState | undefined;
    const controller = new StudyPreferenceController({
      userId: "user-a",
      api: api({ putStudyPreference }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: vi.fn(),
      createCommandKey: () => Promise.resolve("onboarding-preference-command-2"),
      today: () => "2026-07-13",
    });

    await controller.load();
    await controller.submit();
    const pending = loadPendingStudyPreference("user-a");
    expect(current?.pendingRecovery).toBe(true);
    expect(pending?.idempotencyKey).toBe("onboarding-preference-command-2");

    controller.adjustMinutes("weekdayMinutes", 30);
    expect(current?.draft.weekdayMinutes).toBe(45);
    await controller.submit();

    expect(putStudyPreference).toHaveBeenNthCalledWith(
      2,
      pending?.command,
      pending?.idempotencyKey,
    );
    expect(loadPendingStudyPreference("user-a")).toBeNull();
  });

  it("clears a deterministically rejected command so corrected input can create a new version", async () => {
    const putStudyPreference = vi.fn(() =>
      Promise.reject(
        new ApiClientError("建档进度已更新。", 409, "request-2", {
          code: "onboarding_progress_version_conflict",
          message: "建档进度已更新。",
          requestId: "request-2",
        }),
      ),
    );
    let current: StudyPreferenceControllerState | undefined;
    const controller = new StudyPreferenceController({
      userId: "user-a",
      api: api({ putStudyPreference }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: vi.fn(),
      createCommandKey: () => Promise.resolve("onboarding-preference-command-3"),
      today: () => "2026-07-13",
    });

    await controller.load();
    await controller.submit();

    expect(current?.pendingRecovery).toBe(false);
    expect(current?.error?.code).toBe("onboarding_progress_version_conflict");
    expect(loadPendingStudyPreference("user-a")).toBeNull();
    controller.adjustMinutes("weekdayMinutes", 15);
    expect(current?.draft.weekdayMinutes).toBe(60);
  });
});

function api(overrides: Partial<StudyApi> = {}): StudyApi {
  return {
    getProgress: () => Promise.resolve(envelope(progress())),
    getStudyPreference: () => Promise.resolve(envelope(studyResult().preference)),
    putStudyPreference: () => Promise.resolve(envelope(studyResult())),
    ...overrides,
  };
}

function progress(): OnboardingProgress {
  return {
    progressVersion: 2,
    qualificationReady: true,
    qualificationProfileVersion: 1,
    studyPreferenceReady: false,
    studyPreferenceVersion: null,
    resultReady: false,
    resultAcknowledged: false,
    nextStep: "study_preference",
    completed: false,
    updatedAt: "2026-07-13T00:00:00Z",
    completedAt: null,
  };
}

function studyResult(): StudyPreferenceResult {
  return {
    preference: {
      preferenceVersionId: "preference-1",
      preferenceVersion: 1,
      timezone: "Asia/Shanghai",
      effectiveFrom: "2026-07-13",
      weekdayMinutes: 60,
      weekendMinutes: 120,
      longBlockMinutes: 120,
      fragmentMinutes: 25,
      fixedRestDay: "sunday",
      mockExamDay: "saturday",
      sourceType: "user_confirmed",
      createdAt: "2026-07-13T00:00:00Z",
      temporaryAdjustmentOwner: "daily_capacity",
      temporaryAdjustmentEndpoint: "/api/v2/plans/today/capacity",
    },
    progress: {
      ...progress(),
      progressVersion: 3,
      studyPreferenceReady: true,
      studyPreferenceVersion: 1,
      resultReady: true,
      nextStep: "result",
    },
  };
}

function envelope<T>(data: T) {
  return { data, requestId: "request-test", generatedAt: "2026-07-13T00:00:00Z" };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
