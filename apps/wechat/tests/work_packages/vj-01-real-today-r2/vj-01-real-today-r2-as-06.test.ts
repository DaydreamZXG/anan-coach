import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  DailyCapacityCommandResult,
  TodayDashboard,
} from "../../../miniprogram/api/generated/types.gen";
import { prototypeToday } from "../../fixtures/prototype-data";
import {
  apiFailure,
  capacityCommand,
  completedProgress,
  createTodayHarness,
  latestState,
} from "./harness";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("VJ-01 real Today R2 AS-06", () => {
  it("claims the write before awaiting entropy so repeated taps issue one command", async () => {
    let resolveKey!: (value: string) => void;
    const key = new Promise<string>((resolve) => {
      resolveKey = resolve;
    });
    const harness = createTodayHarness({ createIdempotencyKey: () => key });
    await harness.controller.load();
    const command = capacityCommand("capacity-version-1");

    const first = harness.controller.submitCapacity(command);
    const second = harness.controller.submitCapacity(command);
    await Promise.resolve();

    expect(harness.spies.createIdempotencyKey).toHaveBeenCalledOnce();
    expect(harness.spies.setCapacity).not.toHaveBeenCalled();
    resolveKey("single-capacity-key");
    await Promise.all([first, second]);

    expect(harness.spies.setCapacity).toHaveBeenCalledOnce();
    expect(harness.spies.setCapacity).toHaveBeenCalledWith(
      "server-token-a",
      command,
      "single-capacity-key",
    );
  });

  it("never fabricates a plan when the first server read fails", async () => {
    const harness = createTodayHarness();
    harness.spies.getToday.mockRejectedValueOnce(
      apiFailure(503, "service_unavailable", "today-initial-failure"),
    );

    await harness.controller.load();

    expect(latestState(harness.states)).toMatchObject({
      status: "error",
      dashboard: null,
      capacitySubmission: "idle",
      capacityMessage: "",
    });
    expect(harness.states.some((state) => state.capacitySubmission === "success")).toBe(false);
  });

  it("keeps the confirmed plan and idempotency key across a failed write retry", async () => {
    const harness = createTodayHarness();
    await harness.controller.load();
    harness.spies.setCapacity.mockRejectedValueOnce(
      apiFailure(503, "service_unavailable", "capacity-server-failure"),
    );
    const command = capacityCommand("capacity-version-1");

    await harness.controller.submitCapacity(command);

    expect(latestState(harness.states)).toMatchObject({
      status: "ready",
      dashboard: { planVersionId: "server-plan-version-1" },
      capacitySubmission: "error",
      capacityConflictLocked: false,
    });
    expect(latestState(harness.states).capacityMessage).not.toContain("已确认");

    await harness.controller.submitCapacity(command);

    expect(harness.spies.setCapacity).toHaveBeenCalledTimes(2);
    expect(harness.spies.setCapacity.mock.calls[0]?.[2]).toBe(
      harness.spies.setCapacity.mock.calls[1]?.[2],
    );
    expect(latestState(harness.states).capacitySubmission).toBe("success");

    const committedPlan = latestState(harness.states).dashboard;
    harness.spies.getToday.mockRejectedValueOnce(
      apiFailure(503, "service_unavailable", "today-reload-failure"),
    );
    await harness.controller.load();

    expect(latestState(harness.states).dashboard).toEqual(committedPlan);
    expect(latestState(harness.states).capacitySubmission).not.toBe("success");
    expect(latestState(harness.states).capacityMessage).toContain("仍显示上次确认的计划");
  });

  it("keeps the exact page draft visible through submit and failure, then retries the same command", async () => {
    const harness = await createTodayPageHarness();
    const page = harness.page;
    page.onLoad.call(page);

    resolveEnvelope(requiredAt(harness.requests, 0), completedProgress());
    await waitForRequests(harness.requests, 2);
    resolveEnvelope(requiredAt(harness.requests, 1), pageDashboard());
    await waitForPageReady(page, "page-plan-v1");

    page.toggleCapacityEditor.call(page);
    const userDraft = {
      availableMinutes: "75",
      energy: 5,
      stress: 4,
      confidenceLevel: 2,
    };
    page.changeCapacityDraft.call(page, userDraft);

    const firstSubmission = page.submitCapacity.call(page);
    expect(page.data).toMatchObject({
      capacityEditorOpen: true,
      capacityDraft: userDraft,
      capacitySubmission: "submitting",
    });
    await waitForRequests(harness.requests, 3);

    const firstWrite = requiredAt(harness.requests, 2);
    expect(firstWrite.method).toBe("PUT");
    expect(firstWrite.url).toBe("https://kg.xingguoxian.cn/api/v2/plans/today/capacity");
    expect(firstWrite.data).toEqual({
      basedOnCapacityVersionId: "page-capacity-v1",
      availableMinutes: 75,
      energy: 5,
      stress: 4,
      confidenceLevel: 2,
    });
    rejectApi(firstWrite, 503, "service_unavailable", "capacity-page-failure");
    await firstSubmission;

    expect(page.data).toMatchObject({
      capacityEditorOpen: true,
      capacityDraft: userDraft,
      capacitySubmission: "error",
    });
    const firstIdempotencyKey = String(firstWrite.header?.["Idempotency-Key"] ?? "");
    expect(firstIdempotencyKey).toMatch(/^capacity-/u);

    const retry = page.submitCapacity.call(page);
    await waitForRequests(harness.requests, 4);
    const retryWrite = requiredAt(harness.requests, 3);
    expect(retryWrite.data).toEqual(firstWrite.data);
    expect(String(retryWrite.header?.["Idempotency-Key"] ?? "")).toBe(firstIdempotencyKey);

    const confirmedPlan = pageDashboard("page-plan-v2", "page-capacity-v2", {
      availableMinutes: 75,
      energy: 5,
      stress: 4,
      confidenceLevel: 2,
    });
    resolveEnvelope(retryWrite, {
      capacity: confirmedPlan.capacity,
      plan: confirmedPlan,
    } satisfies DailyCapacityCommandResult);
    await retry;

    expect(page.data).toMatchObject({
      displayedPlanVersionId: "page-plan-v2",
      capacityEditorOpen: false,
      capacityDraft: userDraft,
      capacitySubmission: "success",
    });
    expect(harness.getRandomValues).toHaveBeenCalledOnce();
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
}

interface TestTodayPage extends CapturedTodayPage {
  setData(patch: Record<string, unknown>): void;
}

async function createTodayPageHarness(): Promise<{
  page: TestTodayPage;
  requests: WechatMiniprogram.RequestOption[];
  getRandomValues: ReturnType<typeof vi.fn>;
}> {
  let captured: unknown;
  const requests: WechatMiniprogram.RequestOption[] = [];
  const getRandomValues = vi.fn(() => Promise.resolve({ randomValues: new Uint8Array(16).buffer }));
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
    getRandomValues,
    reLaunch: vi.fn(() => Promise.resolve()),
    navigateTo: vi.fn(() => Promise.resolve()),
    switchTab: vi.fn(() => Promise.resolve()),
    showModal: vi.fn(() => Promise.resolve()),
  });

  await import("../../../miniprogram/pages/today/index");
  return {
    page: instantiatePage(captured as CapturedTodayPage),
    requests,
    getRandomValues,
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

async function waitForPageReady(page: TestTodayPage, planVersionId: string): Promise<void> {
  await vi.waitFor(() => {
    expect(page.data).toMatchObject({
      status: "ready",
      displayedPlanVersionId: planVersionId,
      capacitySubmission: "idle",
    });
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
