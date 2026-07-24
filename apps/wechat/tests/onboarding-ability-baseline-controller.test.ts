import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../miniprogram/api/client";
import type {
  AbilityBaselineProfile,
} from "../miniprogram/api/generated/types.gen";
import type { OnboardingApi } from "../miniprogram/api/onboarding";
import {
  AbilityBaselineController,
  type AbilityBaselineControllerState,
} from "../miniprogram/features/onboarding/ability-baseline-controller";
import {
  loadPendingAbilityBaseline,
} from "../miniprogram/features/onboarding/pending-store";

type BaselineApi = Pick<
  OnboardingApi,
  "getAbilityBaseline" | "putAbilityBaseline"
>;

let storage: Map<string, unknown>;

beforeEach(() => {
  storage = new Map();
  vi.stubGlobal("wx", {
    getStorageSync: (key: string) => storage.get(key) ?? "",
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync: (key: string) => storage.delete(key),
    getStorageInfoSync: () => ({
      keys: [...storage.keys()],
      currentSize: 0,
      limitSize: 10_240,
    }),
  });
});

describe("ability baseline controller", () => {
  it("saves a reported score with a durable command and advances", async () => {
    const putAbilityBaseline = vi.fn(() => Promise.resolve(envelope(profile())));
    const advance = vi.fn();
    let current: AbilityBaselineControllerState | undefined;
    const controller = new AbilityBaselineController({
      userId: "user-a",
      api: api({ putAbilityBaseline }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: advance,
      createCommandKey: () => Promise.resolve("ability-baseline-command-1"),
    });

    await controller.load();
    controller.selectMode("reported_score");
    controller.setScore("xingceScore", "65");
    controller.setScore("shenlunScore", "60");
    await controller.submit();

    expect(putAbilityBaseline).toHaveBeenCalledWith(
      {
        expectedBaselineVersion: 0,
        mode: "reported_score",
        xingceScore: 65,
        shenlunScore: 60,
      },
      "ability-baseline-command-1",
    );
    expect(current?.pendingRecovery).toBe(false);
    expect(loadPendingAbilityBaseline("user-a")).toBeNull();
    expect(advance).toHaveBeenCalledTimes(1);
  });

  it("replays the exact command after an uncertain network result", async () => {
    const putAbilityBaseline = vi
      .fn()
      .mockRejectedValueOnce(new ApiClientError("网络请求失败。", 0, "request-1"))
      .mockResolvedValueOnce(envelope(profile()));
    let current: AbilityBaselineControllerState | undefined;
    const controller = new AbilityBaselineController({
      userId: "user-a",
      api: api({ putAbilityBaseline }),
      onChange: (state) => {
        current = state;
      },
      onAdvance: vi.fn(),
      createCommandKey: () => Promise.resolve("ability-baseline-command-2"),
    });

    await controller.load();
    controller.selectMode("unknown");
    await controller.submit();
    const pending = loadPendingAbilityBaseline("user-a");
    expect(current?.pendingRecovery).toBe(true);
    expect(pending?.idempotencyKey).toBe("ability-baseline-command-2");

    controller.selectMode("reported_score");
    expect(current?.draft.mode).toBe("unknown");
    await controller.submit();

    expect(putAbilityBaseline).toHaveBeenNthCalledWith(
      2,
      pending?.command,
      pending?.idempotencyKey,
    );
    expect(loadPendingAbilityBaseline("user-a")).toBeNull();
  });

  it("continues immediately when the baseline already exists", async () => {
    const advance = vi.fn();
    const controller = new AbilityBaselineController({
      userId: "user-a",
      api: api({
        getAbilityBaseline: () => Promise.resolve(envelope(profile())),
      }),
      onChange: vi.fn(),
      onAdvance: advance,
    });

    await controller.load();
    expect(advance).toHaveBeenCalledTimes(1);
  });
});

function api(overrides: Partial<BaselineApi> = {}): BaselineApi {
  return {
    getAbilityBaseline: () =>
      Promise.reject(
        new ApiClientError("尚未确认能力起点。", 404, "request-missing", {
          code: "ability_baseline_missing",
          message: "尚未确认能力起点。",
          requestId: "request-missing",
        }),
      ),
    putAbilityBaseline: () => Promise.resolve(envelope(profile())),
    ...overrides,
  };
}

function profile(): AbilityBaselineProfile {
  return {
    baselineVersionId: "baseline-version-1",
    baselineVersion: 1,
    mode: "reported_score",
    xingceScore: 65,
    shenlunScore: 60,
    scoreEvidenceId: "evidence-1",
    sourceType: "user_confirmed",
    createdAt: "2026-07-24T00:00:00Z",
    supersedesBaselineVersionId: null,
  };
}

function envelope<T>(data: T) {
  return {
    data,
    requestId: "request-test",
    generatedAt: "2026-07-24T00:00:00Z",
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
