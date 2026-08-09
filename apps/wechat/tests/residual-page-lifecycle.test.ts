import { describe, expect, it, vi } from "vitest";

import { ApiClientError } from "../miniprogram/api/client";
import type { OnboardingProgress, TodayDashboard } from "../miniprogram/api/generated/types.gen";
import type { AuthSession } from "../miniprogram/auth/session";
import {
  TodayController,
  type TodayControllerDependencies,
  type TodayControllerState,
} from "../miniprogram/features/planning/today-controller";
import { loadAbilityGoal } from "../miniprogram/pages/ability/features/goal-load";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import abilitySource from "../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import ananSource from "../miniprogram/pages/anan/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import captureSource from "../miniprogram/pages/capture-import/index.ts?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import archiveSource from "../miniprogram/pages/profile/archive/index.ts?raw";

describe("ability goal loading semantics", () => {
  it("distinguishes no data, authentication loss and server failure", async () => {
    const missing = await loadAbilityGoal(true, () =>
      Promise.reject(new ApiClientError("goal missing", 404, "request-404")),
    );
    expect(missing).toEqual({ kind: "missing" });

    const authentication = new ApiClientError("expired", 401, "request-401");
    await expect(loadAbilityGoal(true, () => Promise.reject(authentication))).rejects.toBe(
      authentication,
    );

    const failed = await loadAbilityGoal(true, () =>
      Promise.reject(new ApiClientError("upstream unavailable", 503, "request-503")),
    );
    expect(failed).toEqual({
      kind: "failed",
      message: "upstream unavailable",
      requestId: "request-503",
    });
  });
});

describe("residual page owner wiring", () => {
  it("guards ability and both archive request rounds with a visible exact owner", () => {
    expect(abilitySource).toContain('begin("profile"');
    expect(abilitySource).toContain("requestOwner.owns(");
    expect(abilitySource).toContain("onHide()");
    expect(abilitySource).toContain("onUnload()");

    expect(archiveSource).toContain('begin("archive"');
    expect(String(archiveSource).match(/requestOwner\.owns\(/gu)).toHaveLength(3);
    expect(archiveSource).toContain("onHide()");
    expect(archiveSource).toContain("onUnload()");
  });

  it("guards companion ask completion by page visibility and exact session owner", () => {
    expect(ananSource).toContain('begin("ask"');
    expect(String(ananSource).match(/askOwner\.owns\(/gu)).toHaveLength(3);
    expect(ananSource).toContain("onHide()");
    expect(ananSource).toContain("askOwners.get(this)?.unload()");
  });

  it("guards picker/crop callbacks and closes the record sheet before navigation", () => {
    expect(captureSource).toContain('beginCaptureEffect(this, "picker")');
    expect(captureSource).toContain('beginCaptureEffect(this, "crop")');
    expect(captureSource).toContain("captureEffectIsCurrent(this, effect)");
    expect(captureSource).toContain("navigateFromRecordSheet(this,");
    expect(captureSource).toContain("recordSheetOpen: false");
  });
});

describe("today canonical auth generation ownership", () => {
  it("starts a new load and rejects the old response when only auth generation changes", async () => {
    const oldDashboard = deferred<TodayDashboard>();
    const latestDashboard = dashboard("plan-generation-b");
    const states: TodayControllerState[] = [];
    const session: { current: AuthSession | undefined } = {
      current: {
        userId: "user-a",
        accessToken: "token-a",
        generation: "generation-a",
      },
    };
    const getProgress = vi.fn<TodayControllerDependencies["getProgress"]>(() =>
      Promise.resolve(completedProgress()),
    );
    const getToday = vi
      .fn<TodayControllerDependencies["getToday"]>()
      .mockImplementationOnce(() => oldDashboard.promise)
      .mockResolvedValueOnce(latestDashboard);
    const controller = new TodayController({
      currentSession: () => session.current,
      getProgress,
      getToday,
      setCapacity: vi.fn(),
      createIdempotencyKey: vi.fn(),
      clearSession: vi.fn(),
      reauthenticate: vi.fn(),
      navigateOnboarding: vi.fn(),
      publish: (state) => {
        states.push(state);
      },
    });

    const firstLoad = controller.load();
    await vi.waitFor(() => {
      expect(getToday).toHaveBeenCalledTimes(1);
    });
    const current = session.current;
    if (!current) throw new Error("Expected an active Today session");
    session.current = { ...current, generation: "generation-b" };
    const latestLoad = controller.load();

    try {
      expect(getProgress).toHaveBeenCalledTimes(2);
      await latestLoad;
      oldDashboard.resolve(dashboard("plan-generation-a"));
      await firstLoad;
      expect(states.at(-1)?.dashboard?.planVersionId).toBe("plan-generation-b");
      expect(states.some((state) => state.dashboard?.planVersionId === "plan-generation-a")).toBe(
        false,
      );
    } finally {
      oldDashboard.resolve(dashboard("plan-generation-a"));
      await Promise.allSettled([firstLoad, latestLoad]);
    }
  });
});

function deferred<T>(): { promise: Promise<T>; resolve(value: T): void } {
  let resolvePromise!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    resolvePromise = resolve;
  });
  return { promise, resolve: resolvePromise };
}

function completedProgress(): OnboardingProgress {
  return {
    completed: true,
    completedAt: "2026-08-09T00:00:00Z",
    experienceProfileVersion: 1,
    experienceReady: true,
    nextStep: "workspace",
    progressVersion: 1,
    qualificationProfileVersion: 1,
    qualificationReady: true,
    resultAcknowledged: true,
    resultReady: true,
    studyPreferenceReady: true,
    studyPreferenceVersion: 1,
    updatedAt: "2026-08-09T00:00:00Z",
  };
}

function dashboard(planVersionId: string): TodayDashboard {
  return {
    planVersionId,
    capacity: {
      capacityVersionId: `${planVersionId}-capacity`,
      availableMinutes: 30,
      energy: 3,
      stress: 2,
      confidenceLevel: 4,
    },
  } as unknown as TodayDashboard;
}
