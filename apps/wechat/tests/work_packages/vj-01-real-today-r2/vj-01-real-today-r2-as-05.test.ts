import { afterEach, describe, expect, it, vi } from "vitest";

import type { TodayDashboard } from "../../../miniprogram/api/generated/types.gen";
import { prototypeToday } from "../../../miniprogram/data/prototype-data";
import {
  apiFailure,
  capacityCommand,
  completedProgress,
  createTodayHarness,
  latestState,
  serverDashboard,
} from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-01 real Today R2 AS-05", () => {
  it("locks a stale capacity revision until the server returns a newer version", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();
    harness.spies.setCapacity.mockRejectedValueOnce(
      apiFailure(409, "planning_revision_conflict", "capacity-conflict"),
    );

    await harness.controller.submitCapacity(capacityCommand("capacity-version-1"));

    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: { planVersionId: "server-plan-version-1" },
      capacitySubmission: "conflict",
      capacityConflictLocked: true,
    });
    expect(harness.spies.setCapacity).toHaveBeenCalledOnce();

    harness.controller.acceptLatestDashboard(
      serverDashboard("same-conflicted-plan", "capacity-version-1"),
    );
    expect(latestState(harness.states)).toMatchObject({
      capacitySubmission: "conflict",
      capacityConflictLocked: true,
    });

    await harness.controller.submitCapacity(capacityCommand("capacity-version-1"));
    expect(harness.spies.setCapacity).toHaveBeenCalledOnce();

    harness.spies.getToday.mockResolvedValueOnce(
      serverDashboard("server-plan-version-2", "capacity-version-2"),
    );
    await harness.controller.load();

    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: {
        planVersionId: "server-plan-version-2",
        capacity: { capacityVersionId: "capacity-version-2" },
      },
      capacitySubmission: "idle",
      capacityConflictLocked: false,
    });

    await harness.controller.submitCapacity(capacityCommand("capacity-version-2"));
    expect(harness.spies.setCapacity).toHaveBeenCalledTimes(2);
    expect(latestState(harness.states).capacitySubmission).toBe("success");
  });

  it("keeps the page conflict recovery visible after a failed or same-version reload", async () => {
    const harness = await createTodayPageHarness();
    const page = harness.page;
    page.onLoad.call(page);

    resolveEnvelope(requiredAt(harness.requests, 0), completedProgress());
    await waitForRequests(harness.requests, 2);
    resolveEnvelope(requiredAt(harness.requests, 1), pageDashboard());
    await waitForPageReady(page, "page-plan-v1", "idle");

    page.toggleCapacityEditor.call(page);
    const userDraft = {
      availableMinutes: "85",
      energy: 4,
      stress: 5,
      confidenceLevel: 3,
    };
    page.changeCapacityDraft.call(page, userDraft);
    const conflictSubmission = page.submitCapacity.call(page);
    await waitForRequests(harness.requests, 3);
    rejectApi(requiredAt(harness.requests, 2), 409, "planning_revision_conflict", "conflict-1");
    await conflictSubmission;

    expect(page.data).toMatchObject({
      capacityEditorOpen: true,
      capacityDraft: userDraft,
      capacitySubmission: "conflict",
    });
    const initialConflictMessage = String(page.data.capacityMessage);
    expect(initialConflictMessage).toContain("容量已有更新版本");

    page.reloadAfterCapacityConflict.call(page);
    expect(page.data.capacityEditorOpen).toBe(true);
    await waitForRequests(harness.requests, 4);
    resolveEnvelope(requiredAt(harness.requests, 3), completedProgress());
    await waitForRequests(harness.requests, 5);
    rejectApi(requiredAt(harness.requests, 4), 503, "service_unavailable", "reload-failure");
    await waitForConflictMessage(page, "重新载入失败");

    expect(page.data).toMatchObject({
      capacityEditorOpen: true,
      capacityDraft: userDraft,
      capacitySubmission: "conflict",
    });
    expect(String(page.data.capacityMessage)).toContain(initialConflictMessage);

    page.reloadAfterCapacityConflict.call(page);
    await waitForRequests(harness.requests, 6);
    resolveEnvelope(requiredAt(harness.requests, 5), completedProgress());
    await waitForRequests(harness.requests, 7);
    resolveEnvelope(requiredAt(harness.requests, 6), pageDashboard());
    await waitForConflictMessage(page, "刚在别处改过");

    expect(page.data).toMatchObject({
      capacityEditorOpen: true,
      capacityDraft: userDraft,
      capacitySubmission: "conflict",
    });
    expect(String(page.data.capacityMessage)).toContain("刚在别处改过");

    page.reloadAfterCapacityConflict.call(page);
    await waitForRequests(harness.requests, 8);
    resolveEnvelope(requiredAt(harness.requests, 7), completedProgress());
    await waitForRequests(harness.requests, 9);
    resolveEnvelope(
      requiredAt(harness.requests, 8),
      pageDashboard("page-plan-v2", "page-capacity-v2", {
        availableMinutes: 50,
        energy: 2,
        stress: 3,
        confidenceLevel: 5,
      }),
    );
    await waitForPageReady(page, "page-plan-v2", "idle");

    expect(page.data).toMatchObject({
      capacityEditorOpen: true,
      capacityDraft: {
        availableMinutes: "50",
        energy: 2,
        stress: 3,
        confidenceLevel: 5,
      },
      capacitySubmission: "idle",
      capacityMessage: "",
    });
    page.onUnload.call(page);
  });
});

interface CapturedTodayPage {
  data: Record<string, unknown>;
  onLoad(this: TestTodayPage): void;
  onUnload(this: TestTodayPage): void;
  toggleCapacityEditor(this: TestTodayPage): void;
  changeCapacityDraft(
    this: TestTodayPage,
    changes: {
      availableMinutes: string;
      energy: number;
      stress: number;
      confidenceLevel: number;
    },
  ): void;
  submitCapacity(this: TestTodayPage): Promise<void>;
  reloadAfterCapacityConflict(this: TestTodayPage): void;
}

interface TestTodayPage extends CapturedTodayPage {
  setData(patch: Record<string, unknown>): void;
}

async function createTodayPageHarness(): Promise<{
  page: TestTodayPage;
  requests: WechatMiniprogram.RequestOption[];
}> {
  let captured: unknown;
  const requests: WechatMiniprogram.RequestOption[] = [];
  vi.stubGlobal("Page", (definition: unknown) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({
    globalData: { accessToken: "server-token-a", userId: "user-a" },
  }));
  vi.stubGlobal("wx", {
    getStorageSync: () => "",
    removeStorageSync: vi.fn(),
    request: (options: WechatMiniprogram.RequestOption) => {
      if (options.url.endsWith("/api/v2/plans/daily-opening")) {
        rejectApi(options, 503, "provider_unavailable", "daily-opening-out-of-scope");
        return;
      }
      requests.push(options);
    },
    getRandomValues: vi.fn(() => Promise.resolve({ randomValues: new Uint8Array(16).buffer })),
    reLaunch: vi.fn(() => Promise.resolve()),
    navigateTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve()),
  });

  await import("../../../miniprogram/pages/today/index");
  return {
    page: instantiatePage(captured as CapturedTodayPage),
    requests,
  };
}

function instantiatePage(definition: CapturedTodayPage): TestTodayPage {
  return {
    ...definition,
    data: clone(definition.data),
    setData(patch) {
      Object.assign(this.data, patch);
    },
  };
}

function pageDashboard(
  planVersionId = "page-plan-v1",
  capacityVersionId = "page-capacity-v1",
  capacity: Partial<TodayDashboard["capacity"]> = {},
): TodayDashboard {
  return {
    ...clone(prototypeToday),
    planVersionId,
    capacity: {
      ...prototypeToday.capacity,
      capacityVersionId,
      availableMinutes: 60,
      energy: 3,
      stress: 2,
      confidenceLevel: 4,
      ...capacity,
    },
  };
}

function resolveEnvelope(request: WechatMiniprogram.RequestOption, data: unknown): void {
  request.success?.(
    response(200, {
      data,
      requestId: "page-request-success",
      generatedAt: "2026-07-15T00:00:00Z",
    }),
  );
}

function rejectApi(
  request: WechatMiniprogram.RequestOption,
  statusCode: number,
  code: string,
  requestId: string,
): void {
  request.success?.(
    response(statusCode, {
      code,
      message: code,
      requestId,
    }),
  );
}

function response(
  statusCode: number,
  data: Record<string, unknown>,
): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data,
    statusCode,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}

async function waitForRequests(
  requests: WechatMiniprogram.RequestOption[],
  count: number,
): Promise<void> {
  await vi.waitFor(() => {
    expect(requests.length).toBeGreaterThanOrEqual(count);
  });
}

async function waitForPageReady(
  page: TestTodayPage,
  planVersionId: string,
  capacitySubmission: string,
): Promise<void> {
  await vi.waitFor(() => {
    expect(page.data).toMatchObject({
      status: "ready",
      displayedPlanVersionId: planVersionId,
      capacitySubmission,
    });
  });
}

async function waitForConflictMessage(page: TestTodayPage, message: string): Promise<void> {
  await vi.waitFor(() => {
    expect(page.data).toMatchObject({
      status: "ready",
      capacityEditorOpen: true,
      capacitySubmission: "conflict",
    });
    expect(String(page.data.capacityMessage)).toContain(message);
  });
}

function requiredAt<T>(values: T[], index: number): T {
  const value = values[index];
  if (value === undefined) throw new Error(`missing value at ${String(index)}`);
  return value;
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
