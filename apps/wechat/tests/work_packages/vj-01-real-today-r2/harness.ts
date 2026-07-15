import { vi } from "vitest";

import { ApiClientError } from "../../../miniprogram/api/client";
import type {
  DailyCapacityCommand,
  DailyCapacityCommandResult,
  OnboardingProgress,
  TodayDashboard,
} from "../../../miniprogram/api/generated/types.gen";
import type { AuthSession } from "../../../miniprogram/auth/session";
import {
  TodayController,
  type TodayControllerDependencies,
  type TodayControllerState,
} from "../../../miniprogram/features/planning/today-controller";

export function createTodayHarness(changes: Partial<TodayControllerDependencies> = {}): {
  controller: TodayController;
  states: TodayControllerState[];
  session: { current: AuthSession | undefined };
  spies: {
    getProgress: ReturnType<typeof vi.fn<TodayControllerDependencies["getProgress"]>>;
    getToday: ReturnType<typeof vi.fn<TodayControllerDependencies["getToday"]>>;
    setCapacity: ReturnType<typeof vi.fn<TodayControllerDependencies["setCapacity"]>>;
    createIdempotencyKey: ReturnType<
      typeof vi.fn<TodayControllerDependencies["createIdempotencyKey"]>
    >;
    clearSession: ReturnType<typeof vi.fn>;
    reauthenticate: ReturnType<typeof vi.fn<TodayControllerDependencies["reauthenticate"]>>;
    navigateOnboarding: ReturnType<typeof vi.fn<TodayControllerDependencies["navigateOnboarding"]>>;
  };
} {
  const states: TodayControllerState[] = [];
  const session: { current: AuthSession | undefined } = {
    current: { accessToken: "server-token-a", userId: "user-a" },
  };
  const getProgress = vi.fn<TodayControllerDependencies["getProgress"]>(
    changes.getProgress ?? (() => Promise.resolve(completedProgress())),
  );
  const getToday = vi.fn<TodayControllerDependencies["getToday"]>(
    changes.getToday ?? (() => Promise.resolve(serverDashboard())),
  );
  const setCapacity = vi.fn<TodayControllerDependencies["setCapacity"]>(
    changes.setCapacity ??
      ((_token, command) =>
        Promise.resolve(
          capacityResult(
            serverDashboard("server-plan-after-capacity", "capacity-version-2"),
            command,
          ),
        )),
  );
  const createIdempotencyKey = vi.fn<TodayControllerDependencies["createIdempotencyKey"]>(
    changes.createIdempotencyKey ?? (() => Promise.resolve("capacity-idempotency-key")),
  );
  const clearSession = vi.fn(
    changes.clearSession ??
      (() => {
        session.current = undefined;
      }),
  );
  const reauthenticate = vi.fn<TodayControllerDependencies["reauthenticate"]>(
    changes.reauthenticate ?? (() => Promise.resolve()),
  );
  const navigateOnboarding = vi.fn<TodayControllerDependencies["navigateOnboarding"]>(
    changes.navigateOnboarding ?? (() => Promise.resolve()),
  );
  const dependencies: TodayControllerDependencies = {
    currentSession: changes.currentSession ?? (() => session.current),
    getProgress,
    getToday,
    setCapacity,
    createIdempotencyKey,
    clearSession,
    reauthenticate,
    navigateOnboarding,
    publish:
      changes.publish ??
      ((state) => states.push(JSON.parse(JSON.stringify(state)) as TodayControllerState)),
  };
  return {
    controller: new TodayController(dependencies),
    states,
    session,
    spies: {
      getProgress,
      getToday,
      setCapacity,
      createIdempotencyKey,
      clearSession,
      reauthenticate,
      navigateOnboarding,
    },
  };
}

export function completedProgress(): OnboardingProgress {
  return {
    completed: true,
    completedAt: "2026-07-15T00:00:00Z",
    nextStep: "workspace",
    progressVersion: 4,
    qualificationProfileVersion: 1,
    qualificationReady: true,
    resultAcknowledged: true,
    resultReady: true,
    studyPreferenceReady: true,
    studyPreferenceVersion: 1,
    updatedAt: "2026-07-15T00:00:00Z",
  };
}

export function incompleteProgress(
  nextStep: "qualification" | "study_preference" | "result" = "qualification",
): OnboardingProgress {
  return {
    completed: false,
    completedAt: null,
    nextStep,
    progressVersion: 1,
    qualificationProfileVersion: null,
    qualificationReady: false,
    resultAcknowledged: false,
    resultReady: false,
    studyPreferenceReady: false,
    studyPreferenceVersion: null,
    updatedAt: "2026-07-15T00:00:00Z",
  };
}

export function serverDashboard(
  planVersionId = "server-plan-version-1",
  capacityVersionId = "capacity-version-1",
): TodayDashboard {
  return {
    planVersionId,
    capacity: {
      capacityVersionId,
      availableMinutes: 60,
      energy: 3,
      stress: 2,
      confidenceLevel: 4,
    },
  } as unknown as TodayDashboard;
}

export function capacityCommand(capacityVersionId = "capacity-version-1"): DailyCapacityCommand {
  return {
    basedOnCapacityVersionId: capacityVersionId,
    availableMinutes: 45,
    energy: 4,
    stress: 2,
    confidenceLevel: 4,
  };
}

export function capacityResult(
  plan: TodayDashboard,
  command = capacityCommand(),
): DailyCapacityCommandResult {
  return {
    capacity: {
      ...plan.capacity,
      availableMinutes: command.availableMinutes,
      energy: command.energy,
      stress: command.stress,
      confidenceLevel: command.confidenceLevel,
    },
    plan,
  };
}

export function apiFailure(
  statusCode: number,
  code: string,
  requestId = "request-failure",
): ApiClientError {
  return new ApiClientError(code, statusCode, requestId, {
    code,
    message: code,
    requestId,
  });
}

export function latestState(states: TodayControllerState[]): TodayControllerState {
  const state = states.at(-1);
  if (!state) throw new Error("Today controller did not publish state");
  return state;
}
