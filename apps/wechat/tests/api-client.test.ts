import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installUnauthorizedReset } from "../miniprogram/api/auth";
import { ApiClient, ApiClientError } from "../miniprogram/api/client";
import { currentAuthSession, storeAuthSession } from "../miniprogram/auth/session";

interface TestPayload {
  value: string;
}

let storage: Map<string, unknown>;
let globalData: {
  accessToken?: string;
  userId?: string;
  displayName?: string;
  generation?: string;
};
let removeStorageSync: ReturnType<typeof vi.fn>;

beforeEach(() => {
  storage = new Map();
  globalData = {};
  removeStorageSync = vi.fn((key: string) => {
    storage.delete(key);
  });
  vi.stubGlobal("getApp", () => ({ globalData }));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient", () => {
  it("returns a successful API envelope", async () => {
    vi.stubGlobal(
      "wx",
      withStorage({
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
      }),
    );

    const response = await new ApiClient("https://api.example.test").get<TestPayload>("/value");

    expect(response.data.value).toBe("ok");
    expect(response.requestId).toBe("api-success-1");
  });

  it("preserves the public server error and request ID", async () => {
    vi.stubGlobal(
      "wx",
      withStorage({
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
      }),
    );

    const failure = await new ApiClient("https://api.example.test")
      .post("/value", { value: "stale" })
      .catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiClientError);
    expect(failure).toMatchObject({
      message: "数据已经更新，请刷新后重试。",
      statusCode: 409,
      requestId: "api-conflict-1",
      payload: { code: "revision_conflict" },
      classification: "http_response",
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
    vi.stubGlobal("wx", withStorage({ request }));

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

  it("does not clear a newly logged-in session when an old request later returns 401", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal(
      "wx",
      withStorage({
        request: vi.fn((options: WechatMiniprogram.RequestOption) => requests.push(options)),
      }),
    );
    storeAuthSession("old-token", "user-a");
    installUnauthorizedReset();

    const oldRequest = new ApiClient("https://api.example.test", "old-token").get("/protected");
    storeAuthSession("new-token", "user-a");
    respondUnauthorized(requests[0]);

    await expect(oldRequest).rejects.toMatchObject({ statusCode: 401 });
    expect(currentAuthSession()).toMatchObject({ accessToken: "new-token", userId: "user-a" });
  });

  it("clears the current session once for concurrent 401 responses from the same generation", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal(
      "wx",
      withStorage({
        request: vi.fn((options: WechatMiniprogram.RequestOption) => requests.push(options)),
      }),
    );
    storeAuthSession("token-a", "user-a");
    installUnauthorizedReset();

    const first = new ApiClient("https://api.example.test", "token-a").get("/first");
    const second = new ApiClient("https://api.example.test", "token-a").get("/second");
    respondUnauthorized(requests[0]);
    respondUnauthorized(requests[1]);

    await expect(
      Promise.all([
        first.catch((error: unknown) => error),
        second.catch((error: unknown) => error),
      ]),
    ).resolves.toHaveLength(2);
    expect(currentAuthSession()).toBeUndefined();
    expect(
      removeStorageSync.mock.calls.filter(([key]) => key === "gongkao.auth.session.v1"),
    ).toHaveLength(1);
  });

  it("does not clear a reauthenticated same-token session with a new generation", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal(
      "wx",
      withStorage({
        request: vi.fn((options: WechatMiniprogram.RequestOption) => requests.push(options)),
      }),
    );
    storeAuthSession("shared-token", "user-a");
    installUnauthorizedReset();

    const oldRequest = new ApiClient("https://api.example.test", "shared-token").get("/protected");
    const oldGeneration = currentAuthSession()?.generation;
    storeAuthSession("shared-token", "user-a");
    const newGeneration = currentAuthSession()?.generation;
    respondUnauthorized(requests[0]);

    await expect(oldRequest).rejects.toMatchObject({ statusCode: 401 });
    expect(newGeneration).not.toBe(oldGeneration);
    expect(currentAuthSession()).toMatchObject({ generation: newGeneration });
  });

  it("classifies transport failures as unknown outcomes", async () => {
    vi.stubGlobal(
      "wx",
      withStorage({
        request: vi.fn((options: WechatMiniprogram.RequestOption) => {
          options.fail?.({
            errMsg: "request:fail timeout",
            errno: -1,
            exception: { reasons: [], retryCount: 0 },
            useHttpDNS: false,
          });
        }),
      }),
    );

    const failure = await new ApiClient("https://api.example.test")
      .post("/write", { value: "x" })
      .catch((error: unknown) => error);

    expect(failure).toMatchObject({
      statusCode: 0,
      classification: "transport_unknown",
    });
  });
});

function withStorage(overrides: Record<string, unknown>): Record<string, unknown> {
  return {
    getStorageSync: (key: string) => storage.get(key),
    setStorageSync: (key: string, value: unknown) => storage.set(key, clone(value)),
    removeStorageSync,
    ...overrides,
  };
}

function respondUnauthorized(options: WechatMiniprogram.RequestOption | undefined): void {
  options?.success?.({
    data: {
      code: "authentication_required",
      message: "登录状态已失效。",
      requestId: "api-auth-401",
    },
    statusCode: 401,
    header: {},
    cookies: [],
    exception: { reasons: [], retryCount: 0 },
    profile: {} as WechatMiniprogram.RequestProfile,
    useHttpDNS: false,
    errMsg: "request:ok",
  });
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
