import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../miniprogram/api/client";
import type {
  OnboardingProgress,
  QualificationProfile,
} from "../miniprogram/api/generated/types.gen";
import type { OnboardingApi } from "../miniprogram/api/onboarding";
import type { QualificationApi } from "../miniprogram/api/qualification";
import {
  QualificationController,
  type QualificationControllerState,
} from "../miniprogram/features/onboarding/qualification-controller";
import {
  loadPendingQualification,
  ONBOARDING_PENDING_TTL_MS,
  storePendingQualification,
} from "../miniprogram/features/onboarding/pending-store";
import {
  qualificationFactsFromDraft,
  type QualificationDraft,
} from "../miniprogram/features/qualification/onboarding";

type ProfileApi = Pick<QualificationApi, "getProfile" | "updateProfile">;
type ProgressApi = Pick<OnboardingApi, "getProgress" | "completeQualification">;

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", storageWx(storage));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("qualification controller", () => {
  it("lets a newer load own state when an older progress response arrives later", async () => {
    const first = deferred<ReturnType<typeof envelope<OnboardingProgress>>>();
    const second = deferred<ReturnType<typeof envelope<OnboardingProgress>>>();
    const getProgress = vi
      .fn<ProgressApi["getProgress"]>()
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    let current: QualificationControllerState | undefined;
    const controller = createController({
      progressApi: progressApi({ getProgress }),
      profileApi: profileApi({
        getProfile: () => Promise.resolve(envelope(profile(validDraft({ major: "新闻学" }), 4))),
      }),
      onChange: (state) => {
        current = state;
      },
    });

    const olderLoad = controller.load();
    const newerLoad = controller.load();
    second.resolve(envelope(progress({ progressVersion: 7 })));
    await newerLoad;
    expect(current?.draft.major).toBe("新闻学");
    expect(current?.profileVersion).toBe(4);

    first.resolve(envelope(progress({ progressVersion: 2 })));
    await olderLoad;
    expect(current?.draft.major).toBe("新闻学");
    expect(current?.profileVersion).toBe(4);
  });

  it("coalesces a double submit into one milestone command", async () => {
    const completeQualification = vi.fn(() =>
      Promise.resolve(envelope(progress({ progressVersion: 3, nextStep: "study_preference" }))),
    );
    const createCommandKey = vi.fn(() => Promise.resolve("qualification-milestone-command-1"));
    const controller = createController({
      progressApi: progressApi({ completeQualification }),
      createCommandKey,
    });
    await controller.load();

    const first = controller.submit();
    const second = controller.submit();
    await Promise.all([first, second]);

    expect(completeQualification).toHaveBeenCalledTimes(1);
    expect(createCommandKey).toHaveBeenCalledTimes(1);
    expect(controller.snapshot().saved).toBe(true);
    expect(loadPendingQualification("user-a")).toBeNull();
  });

  it("replays the exact profile command after an unknown network outcome", async () => {
    const updated = profile(validDraft({ major: "新闻学" }), 3);
    const updateProfile = vi
      .fn<ProfileApi["updateProfile"]>()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-unknown"))
      .mockResolvedValueOnce(envelope(updated));
    const completeQualification = vi.fn(() =>
      Promise.resolve(envelope(progress({ progressVersion: 3, nextStep: "study_preference" }))),
    );
    const createCommandKey = vi
      .fn<(kind: "profile" | "milestone") => Promise<string>>()
      .mockResolvedValueOnce("qualification-profile-command-1")
      .mockResolvedValueOnce("qualification-milestone-command-2");
    const controller = createController({
      profileApi: profileApi({ updateProfile }),
      progressApi: progressApi({ completeQualification }),
      createCommandKey,
    });
    await controller.load();
    controller.changeDraft({ major: "新闻学" });

    await controller.submit();
    const pending = loadPendingQualification("user-a");
    expect(pending).toMatchObject({
      phase: "profile",
      idempotencyKey: "qualification-profile-command-1",
    });
    expect(controller.snapshot().pendingRecovery).toBe(true);

    await controller.submit();
    expect(updateProfile).toHaveBeenNthCalledWith(
      2,
      pending?.phase === "profile" ? pending.facts : undefined,
      "qualification-profile-command-1",
    );
    expect(createCommandKey).toHaveBeenCalledTimes(2);
    expect(completeQualification).toHaveBeenCalledTimes(1);
    expect(loadPendingQualification("user-a")).toBeNull();
  });

  it("reuses the exact milestone command when the completion result is unknown", async () => {
    const accepted = envelope(progress({ progressVersion: 3, nextStep: "study_preference" }));
    const completeQualification = vi
      .fn<ProgressApi["completeQualification"]>()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-unknown"))
      .mockResolvedValueOnce(accepted);
    const controller = createController({
      progressApi: progressApi({ completeQualification }),
      createCommandKey: () => Promise.resolve("qualification-milestone-command-retry"),
    });
    await controller.load();

    await controller.submit();
    const pending = loadPendingQualification("user-a");
    expect(pending).toMatchObject({
      phase: "milestone",
      idempotencyKey: "qualification-milestone-command-retry",
    });

    controller.next();
    await flushPromises();
    expect(completeQualification).toHaveBeenNthCalledWith(
      2,
      pending?.phase === "milestone" ? pending.command : undefined,
      "qualification-milestone-command-retry",
    );
    expect(controller.snapshot().saved).toBe(true);
    expect(loadPendingQualification("user-a")).toBeNull();
  });

  it("reloads the canonical profile after a 409 version conflict", async () => {
    const firstProfile = profile(validDraft(), 2);
    const currentProfile = profile(validDraft({ major: "新闻学" }), 5);
    const getProfile = vi
      .fn<ProfileApi["getProfile"]>()
      .mockResolvedValueOnce(envelope(firstProfile))
      .mockResolvedValueOnce(envelope(currentProfile));
    const getProgress = vi
      .fn<ProgressApi["getProgress"]>()
      .mockResolvedValueOnce(envelope(progress({ progressVersion: 2 })))
      .mockResolvedValueOnce(envelope(progress({ progressVersion: 2 })))
      .mockResolvedValueOnce(envelope(progress({ progressVersion: 4 })));
    const completeQualification = vi.fn(() =>
      Promise.reject(
        new ApiClientError("建档进度已更新，请刷新后继续。", 409, "request-conflict", {
          code: "onboarding_progress_version_conflict",
          message: "建档进度已更新，请刷新后继续。",
          requestId: "request-conflict",
        }),
      ),
    );
    const controller = createController({
      profileApi: profileApi({ getProfile }),
      progressApi: progressApi({ getProgress, completeQualification }),
      createCommandKey: () => Promise.resolve("qualification-milestone-command-3"),
    });

    await controller.load();
    await controller.submit();

    expect(getProgress).toHaveBeenCalledTimes(3);
    expect(controller.snapshot()).toMatchObject({
      status: "ready",
      profileVersion: 5,
      draft: { major: "新闻学" },
      saving: false,
      pendingRecovery: false,
      error: { code: "onboarding_progress_version_conflict" },
    });
    expect(loadPendingQualification("user-a")).toBeNull();
  });

  it("drops an expired recovery command and restores only server facts", async () => {
    storePendingQualification(
      "user-a",
      {
        phase: "profile",
        idempotencyKey: "qualification-profile-expired",
        draft: validDraft({ major: "过期专业" }),
        facts: qualificationFactsFromDraft(validDraft({ major: "过期专业" })),
      },
      1_000,
    );
    vi.useFakeTimers();
    vi.setSystemTime(1_000 + ONBOARDING_PENDING_TTL_MS);
    const controller = createController();

    await controller.load();

    expect(controller.snapshot()).toMatchObject({
      pendingRecovery: false,
      draft: { major: "法学" },
    });
    expect(loadPendingQualification("user-a")).toBeNull();
  });
});

function createController(
  options: {
    profileApi?: ProfileApi;
    progressApi?: ProgressApi;
    onChange?: (state: QualificationControllerState) => void;
    createCommandKey?: (kind: "profile" | "milestone") => Promise<string>;
  } = {},
): QualificationController {
  return new QualificationController({
    userId: "user-a",
    profileApi: options.profileApi ?? profileApi(),
    progressApi: options.progressApi ?? progressApi(),
    onChange: options.onChange ?? vi.fn(),
    onAdvance: vi.fn(),
    ...(options.createCommandKey ? { createCommandKey: options.createCommandKey } : {}),
  });
}

function profileApi(overrides: Partial<ProfileApi> = {}): ProfileApi {
  return {
    getProfile: () => Promise.resolve(envelope(profile(validDraft(), 2))),
    updateProfile: () => Promise.resolve(envelope(profile(validDraft(), 3))),
    ...overrides,
  };
}

function progressApi(overrides: Partial<ProgressApi> = {}): ProgressApi {
  return {
    getProgress: () => Promise.resolve(envelope(progress())),
    completeQualification: () =>
      Promise.resolve(envelope(progress({ progressVersion: 3, nextStep: "study_preference" }))),
    ...overrides,
  };
}

function validDraft(changes: Partial<QualificationDraft> = {}): QualificationDraft {
  return {
    education: "本科",
    major: "法学",
    freshGraduate: "true",
    politicalStatus: "群众",
    grassrootsYears: 0,
    hukou: "",
    ...changes,
  };
}

function profile(draft: QualificationDraft, profileVersion: number): QualificationProfile {
  return {
    profileVersion,
    facts: qualificationFactsFromDraft(draft).map((fact, index) => ({
      ...fact,
      factId: `fact-${String(index)}`,
      verificationStatus: "reported" as const,
      evidence: [],
      effectiveAt: "2026-07-14T00:00:00Z",
      supersedesFactId: null,
    })),
  };
}

function progress(changes: Partial<OnboardingProgress> = {}): OnboardingProgress {
  return {
    progressVersion: 2,
    experienceReady: true,
    experienceProfileVersion: 1,
    qualificationReady: false,
    qualificationProfileVersion: null,
    studyPreferenceReady: false,
    studyPreferenceVersion: null,
    resultReady: false,
    resultAcknowledged: false,
    nextStep: "qualification",
    completed: false,
    updatedAt: "2026-07-14T00:00:00Z",
    completedAt: null,
    ...changes,
  };
}

function envelope<T>(data: T) {
  return { data, requestId: "request-test", generatedAt: "2026-07-14T00:00:00Z" };
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

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function flushPromises(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
