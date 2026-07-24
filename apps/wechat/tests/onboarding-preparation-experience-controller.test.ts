import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../miniprogram/api/client";
import type {
  OnboardingProgress,
  PreparationExperienceResult,
} from "../miniprogram/api/generated/types.gen";
import type { OnboardingApi } from "../miniprogram/api/onboarding";
import {
  PreparationExperienceController,
  type PreparationExperienceControllerState,
} from "../miniprogram/features/onboarding/preparation-experience-controller";
import { loadPendingPreparationExperience } from "../miniprogram/features/onboarding/pending-store";

type ExperienceApi = Pick<OnboardingApi, "getProgress" | "putPreparationExperience">;
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

describe("preparation experience controller", () => {
  it("enforces the retake relationship and advances from the server response", async () => {
    const putPreparationExperience = vi.fn(() => Promise.resolve(envelope(result())));
    const onAdvance = vi.fn();
    let current: PreparationExperienceControllerState | undefined;
    const controller = new PreparationExperienceController({
      userId: "user-a",
      api: api({ putPreparationExperience }),
      onChange: (state) => {
        current = state;
      },
      onAdvance,
      createCommandKey: () => Promise.resolve("preparation-command-0001"),
    });

    await controller.load();
    controller.select("retake");
    controller.selectCurrentStatus("parttime");
    expect(current?.draft.retake).toBe(true);
    expect(current?.draft.priorGuokaoExam).toBe(true);
    await controller.submit();

    expect(putPreparationExperience).toHaveBeenCalledWith(
      {
        expectedExperienceVersion: 0,
        expectedProgressVersion: 1,
        currentStatus: "parttime",
        firstSystematic: false,
        externalLearning: false,
        priorGuokaoExam: true,
        retake: true,
        priorExamYears: [],
      },
      "preparation-command-0001",
    );
    expect(loadPendingPreparationExperience("user-a")).toBeNull();
    expect(onAdvance).toHaveBeenCalledWith("qualification");
  });

  it("replays the same durable command after an uncertain response", async () => {
    const putPreparationExperience = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-1"))
      .mockResolvedValueOnce(envelope(result()));
    let current: PreparationExperienceControllerState | undefined;
    const controller = new PreparationExperienceController({
      userId: "user-a",
      api: api({ putPreparationExperience }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: vi.fn(),
      createCommandKey: () => Promise.resolve("preparation-command-0002"),
    });

    await controller.load();
    controller.select("firstSystematic");
    controller.selectCurrentStatus("fulltime");
    await controller.submit();
    const pending = loadPendingPreparationExperience("user-a");
    expect(current?.pendingRecovery).toBe(true);
    controller.select("externalLearning");
    expect(current?.draft.firstSystematic).toBe(true);

    await controller.submit();
    expect(putPreparationExperience).toHaveBeenNthCalledWith(
      2,
      pending?.command,
      pending?.idempotencyKey,
    );
    expect(loadPendingPreparationExperience("user-a")).toBeNull();
  });
});

function api(overrides: Partial<ExperienceApi> = {}): ExperienceApi {
  return {
    getProgress: () => Promise.resolve(envelope(progress())),
    putPreparationExperience: () => Promise.resolve(envelope(result())),
    ...overrides,
  };
}

function progress(): OnboardingProgress {
  return {
    progressVersion: 1,
    experienceReady: false,
    experienceProfileVersion: null,
    qualificationReady: false,
    qualificationProfileVersion: null,
    studyPreferenceReady: false,
    studyPreferenceVersion: null,
    resultReady: false,
    resultAcknowledged: false,
    nextStep: "preparation_experience",
    completed: false,
    updatedAt: "2026-07-18T00:00:00Z",
    completedAt: null,
  };
}

function result(): PreparationExperienceResult {
  return {
    experience: {
      experienceVersionId: "experience-1",
      experienceVersion: 1,
      declarationStatus: "declared",
      currentStatus: "parttime",
      firstSystematic: false,
      externalLearning: false,
      priorGuokaoExam: true,
      retake: true,
      priorExamYears: [],
      sourceType: "user_confirmed",
      recommendedEntry: "retake_loss_recovery",
      createdAt: "2026-07-18T00:00:00Z",
      supersedesExperienceVersionId: null,
    },
    progress: {
      ...progress(),
      progressVersion: 2,
      experienceReady: true,
      experienceProfileVersion: 1,
      nextStep: "qualification",
    },
  };
}

function envelope<T>(data: T) {
  return { data, requestId: "request-test", generatedAt: "2026-07-18T00:00:00Z" };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
