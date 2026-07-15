import { afterEach, describe, expect, it, vi } from "vitest";
import { AccountApi } from "../miniprogram/api/account";
import { ApiClient } from "../miniprogram/api/client";

afterEach(() => {
  vi.unstubAllGlobals();
});

function respond(options: WechatMiniprogram.RequestOption, data: object): void {
  options.success?.({
    data: {
      data,
      requestId: "account-request-1",
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
}

describe("AccountApi", () => {
  it("loads the authenticated account and device sessions", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/auth/account");
      expect(options.method).toBe("GET");
      expect(options.header).toMatchObject({ Authorization: "Bearer session-token" });
      respond(options, {
        accountId: "account-1",
        accountStatus: "active",
        wechatLinked: true,
        profileVersion: 3,
        establishedAt: "2026-07-01T12:00:00Z",
        sessions: [],
      });
    });
    vi.stubGlobal("wx", { request });

    const result = await new AccountApi(
      new ApiClient("https://api.example.test", "session-token"),
    ).getOverview();

    expect(result.data.profileVersion).toBe(3);
  });

  it("revokes only the selected encoded session", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/auth/sessions/session%2F1");
      expect(options.method).toBe("DELETE");
      respond(options, { sessionId: "session/1", revoked: true });
    });
    vi.stubGlobal("wx", { request });

    const result = await new AccountApi(
      new ApiClient("https://api.example.test", "session-token"),
    ).revokeSession("session/1");

    expect(result.data.revoked).toBe(true);
  });

  it.each([
    ["current session", "logoutCurrent", "/api/v2/auth/logout"],
    ["all sessions", "logoutAll", "/api/v2/auth/logout-all"],
  ] as const)("logs out the %s", async (_label, method, path) => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe(`https://api.example.test${path}`);
      expect(options.method).toBe("POST");
      expect(options.data).toEqual({});
      respond(options, { revokedSessionCount: 1 });
    });
    vi.stubGlobal("wx", { request });
    const api = new AccountApi(new ApiClient("https://api.example.test", "session-token"));

    const result = await api[method]();

    expect(result.data.revokedSessionCount).toBe(1);
  });
});
