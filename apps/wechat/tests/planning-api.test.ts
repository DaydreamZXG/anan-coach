import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { PlanningApi } from "../miniprogram/api/planning";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PlanningApi", () => {
  it("records capacity against an immutable base version", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/plans/today/capacity");
      expect(options.method).toBe("PUT");
      expect(options.header).toMatchObject({
        Authorization: "Bearer token",
        "Idempotency-Key": "capacity-command-1",
      });
      expect(options.data).toEqual({
        basedOnCapacityVersionId: "00000000-0000-4000-8000-000000000001",
        availableMinutes: 45,
        energy: 2,
        stress: 4,
        confidenceLevel: 3,
      });
      options.success?.(success({ capacity: {}, plan: {} }));
    });
    vi.stubGlobal("wx", { request });
    const api = new PlanningApi(new ApiClient("https://api.example.test", "token"));

    await api.setTodayCapacity(
      {
        basedOnCapacityVersionId: "00000000-0000-4000-8000-000000000001",
        availableMinutes: 45,
        energy: 2,
        stress: 4,
        confidenceLevel: 3,
      },
      "capacity-command-1",
    );

    expect(request).toHaveBeenCalledOnce();
  });

  it("records a version-aware task execution command", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe(
        "https://api.example.test/api/v2/plans/tasks/task%2Fwith%2Fslash/execution",
      );
      expect(options.method).toBe("PUT");
      expect(options.header).toMatchObject({ "Idempotency-Key": "execution-command-1" });
      expect(options.data).toEqual({
        status: "in_progress",
        elapsedMinutes: 8,
        evidenceRefs: [],
        basedOnExecutionVersionId: null,
      });
      options.success?.(success({ execution: {}, plan: {}, replanned: true }));
    });
    vi.stubGlobal("wx", { request });
    const api = new PlanningApi(new ApiClient("https://api.example.test", "token"));

    await api.recordTaskExecution(
      "task/with/slash",
      {
        status: "in_progress",
        elapsedMinutes: 8,
        evidenceRefs: [],
        basedOnExecutionVersionId: null,
      },
      "execution-command-1",
    );

    expect(request).toHaveBeenCalledOnce();
  });
});

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "planning-request-1",
      generatedAt: "2026-07-13T00:00:00Z",
    },
    statusCode: 200,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  };
}
