import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient, ApiClientError } from "../miniprogram/api/client";

interface TestPayload {
  value: string;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient", () => {
  it("returns a successful API envelope", async () => {
    vi.stubGlobal("wx", {
      request: vi.fn((options: WechatMiniprogram.RequestOption) => {
        options.success?.({
          data: {
            data: { value: "ok" },
            requestId: "api-success-1",
            generatedAt: "2026-07-12T12:00:00Z",
          },
          statusCode: 200,
          header: {},
          cookies: [],
          exception: { reasons: [], retryCount: 0 },
          profile: {} as WechatMiniprogram.RequestProfile,
          useHttpDNS: false,
          errMsg: "request:ok",
        });
      }),
    });

    const response = await new ApiClient("https://api.example.test").get<TestPayload>("/value");

    expect(response.data.value).toBe("ok");
    expect(response.requestId).toBe("api-success-1");
  });

  it("preserves the public server error and request ID", async () => {
    vi.stubGlobal("wx", {
      request: vi.fn((options: WechatMiniprogram.RequestOption) => {
        options.success?.({
          data: {
            code: "revision_conflict",
            message: "数据已经更新，请刷新后重试。",
            requestId: "api-conflict-1",
          },
          statusCode: 409,
          header: {},
          cookies: [],
          exception: { reasons: [], retryCount: 0 },
          profile: {} as WechatMiniprogram.RequestProfile,
          useHttpDNS: false,
          errMsg: "request:ok",
        });
      }),
    });

    const failure = await new ApiClient("https://api.example.test")
      .post("/value", { value: "stale" })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiClientError);
    expect(failure).toMatchObject({
      message: "数据已经更新，请刷新后重试。",
      statusCode: 409,
      requestId: "api-conflict-1",
      payload: { code: "revision_conflict" },
    });
  });

  it("adds request and idempotency headers to writes", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.header).toMatchObject({
        Authorization: "Bearer session-token",
        "Idempotency-Key": "attempt-1",
        "X-Task-Edit-Lease": "00000000-0000-4000-8000-000000000903",
        "X-Task-Edit-Generation": "4",
      });
      expect(String(options.header?.["X-Request-ID"])).toMatch(/^mp-/u);
      options.success?.({
        data: {
          data: { value: "saved" },
          requestId: "api-write-1",
          generatedAt: "2026-07-12T12:00:00Z",
        },
        statusCode: 201,
        header: {},
        cookies: [],
        exception: { reasons: [], retryCount: 0 },
        profile: {} as WechatMiniprogram.RequestProfile,
        useHttpDNS: false,
        errMsg: "request:ok",
      });
    });
    vi.stubGlobal("wx", { request });

    await new ApiClient("https://api.example.test", "session-token").post<TestPayload>(
      "/attempts",
      { answer: "B" },
      {
        idempotencyKey: "attempt-1",
        taskEditLease: {
          leaseId: "00000000-0000-4000-8000-000000000903",
          generation: 4,
        },
      },
    );

    expect(request).toHaveBeenCalledOnce();
  });
});
