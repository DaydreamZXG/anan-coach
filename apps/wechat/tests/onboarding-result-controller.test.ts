import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../miniprogram/api/client";
import type {
  OnboardingProgress,
  OnboardingResultProjection,
} from "../miniprogram/api/generated/types.gen";
import type { OnboardingApi } from "../miniprogram/api/onboarding";
import {
  OnboardingResultController,
  type OnboardingResultControllerState,
} from "../miniprogram/pages/onboarding/features/result-controller";
import { loadPendingResultAcknowledgement } from "../miniprogram/features/onboarding/pending-store";

type ResultApi = Pick<OnboardingApi, "getProgress" | "getResult" | "acknowledgeResult">;

beforeEach(() => {
  const storage = new Map<string, unknown>();
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({ keys: [...storage.keys()], currentSize: 0, limitSize: 10_240 }),
  });
});

describe("onboarding result controller", () => {
  it("acknowledges the server projection before following its explicit action", async () => {
    const acknowledgeResult = vi.fn(() => Promise.resolve(envelope(completedProgress())));
    const advance = vi.fn();
    const controller = new OnboardingResultController({
      userId: "user-a",
      api: api({ acknowledgeResult }),
      onChange: vi.fn(),
      onAdvance: advance,
      createCommandKey: () => Promise.resolve("onboarding-result-command-1"),
    });

    await controller.load();
    await controller.acknowledge();

    expect(acknowledgeResult).toHaveBeenCalledWith(
      { expectedProgressVersion: 3, acknowledgeResult: true },
      "onboarding-result-command-1",
    );
    expect(loadPendingResultAcknowledgement("user-a")).toBeNull();
    expect(advance).toHaveBeenCalledWith({ kind: "action", action: "enter_workspace" });
  });

  it("locks the same acknowledgement command across an unknown network result", async () => {
    const acknowledgeResult = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-unknown"))
      .mockResolvedValueOnce(envelope(completedProgress()));
    let current: OnboardingResultControllerState | undefined;
    const controller = new OnboardingResultController({
      userId: "user-a",
      api: api({ acknowledgeResult }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: vi.fn(),
      createCommandKey: () => Promise.resolve("onboarding-result-command-2"),
    });

    await controller.load();
    await controller.acknowledge();
    const pending = loadPendingResultAcknowledgement("user-a");
    expect(current?.pendingRecovery).toBe(true);

    await controller.acknowledge();
    expect(acknowledgeResult).toHaveBeenNthCalledWith(2, pending?.command, pending?.idempotencyKey);
    expect(loadPendingResultAcknowledgement("user-a")).toBeNull();
  });

  it("reconciles a stale acknowledgement against server progress", async () => {
    const getProgress = vi
      .fn<ResultApi["getProgress"]>()
      .mockResolvedValueOnce(envelope(resultProgress()))
      .mockResolvedValueOnce(envelope(completedProgress()));
    const acknowledgeResult = vi.fn(() =>
      Promise.reject(
        new ApiClientError("建档进度已更新。", 409, "request-conflict", {
          code: "onboarding_progress_version_conflict",
          message: "建档进度已更新。",
          requestId: "request-conflict",
        }),
      ),
    );
    const advance = vi.fn();
    const controller = new OnboardingResultController({
      userId: "user-a",
      api: api({ getProgress, acknowledgeResult }),
      onChange: vi.fn(),
      onAdvance: advance,
      createCommandKey: () => Promise.resolve("onboarding-result-command-3"),
    });

    await controller.load();
    await controller.acknowledge();

    expect(getProgress).toHaveBeenCalledTimes(2);
    expect(loadPendingResultAcknowledgement("user-a")).toBeNull();
    expect(advance).toHaveBeenCalledWith({ kind: "action", action: "enter_workspace" });
  });
});

function api(overrides: Partial<ResultApi> = {}): ResultApi {
  return {
    getProgress: () => Promise.resolve(envelope(resultProgress())),
    getResult: () => Promise.resolve(envelope(resultProjection())),
    acknowledgeResult: () => Promise.resolve(envelope(completedProgress())),
    ...overrides,
  };
}

function resultProgress(): OnboardingProgress {
  return {
    ...completedProgress(),
    progressVersion: 3,
    resultAcknowledged: false,
    nextStep: "result",
    completed: false,
    completedAt: null,
  };
}

function resultProjection(): OnboardingResultProjection {
  return {
    status: "position_selection_required",
    reasonCode: "primary_goal_missing",
    progressVersion: 3,
    qualificationProfileVersion: 2,
    preparationExperience: preparationExperience(),
    abilityEstimate: null,
    primaryGoal: null,
    scoreGap: null,
    weeklyPreference: {
      preferenceVersionId: "preference-1",
      preferenceVersion: 1,
      timezone: "Asia/Shanghai",
      effectiveFrom: "2026-07-13",
      weekdayMinutes: 45,
      weekendMinutes: 120,
      longBlockMinutes: 120,
      fragmentMinutes: 25,
      fixedRestDay: "sunday",
      mockExamDay: "saturday",
      sourceType: "user_confirmed",
      createdAt: "2026-07-13T00:00:00Z",
    },
    nextAction: "enter_workspace",
    generatedAt: "2026-07-13T00:00:00Z",
    validUntil: "2027-07-12T00:00:00Z",
  };
}

function completedProgress(): OnboardingProgress {
  return {
    progressVersion: 4,
    experienceReady: true,
    experienceProfileVersion: 1,
    qualificationReady: true,
    qualificationProfileVersion: 2,
    studyPreferenceReady: true,
    studyPreferenceVersion: 1,
    resultReady: true,
    resultAcknowledged: true,
    nextStep: "workspace",
    completed: true,
    updatedAt: "2026-07-13T00:01:00Z",
    completedAt: "2026-07-13T00:01:00Z",
  };
}

function preparationExperience() {
  return {
    experienceVersionId: "experience-1",
    experienceVersion: 1,
    declarationStatus: "declared" as const,
    currentStatus: "parttime" as const,
    firstSystematic: true,
    externalLearning: false,
    priorGuokaoExam: false,
    retake: false,
    priorExamYears: [],
    sourceType: "user_confirmed" as const,
    recommendedEntry: "guided_first_start" as const,
    createdAt: "2026-07-13T00:00:00Z",
    supersedesExperienceVersionId: null,
  };
}

function envelope<T>(data: T) {
  return { data, requestId: "request-test", generatedAt: "2026-07-13T00:00:00Z" };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
