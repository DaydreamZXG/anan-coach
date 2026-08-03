import { afterEach, describe, expect, it, vi } from "vitest";

import { configureForWechatEnvVersion, environment } from "../../../miniprogram/config/environment";
import { createCoachRepository } from "../../../miniprogram/data/coach-repository";

const originalChannel = environment.releaseChannel;
const originalApiBaseUrl = environment.apiBaseUrl;

afterEach(() => {
  environment.releaseChannel = originalChannel;
  environment.apiBaseUrl = originalApiBaseUrl;
  vi.unstubAllGlobals();
});

describe("VJ-01 real Today R2 AS-01 client chain", () => {
  it("uses the authenticated server repository in develop", async () => {
    expect(configureForWechatEnvVersion("develop")).toMatchObject({
      releaseChannel: "development",
    });

    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://kg.xingguoxian.cn/api/v2/plans/today");
      expect(options.method).toBe("GET");
      expect(options.header).toMatchObject({ Authorization: "Bearer server-session" });
      options.success?.(success({ planVersionId: "server-plan-version" }));
    });
    vi.stubGlobal("wx", { request });

    const dashboard = await createCoachRepository("server-session").getToday();

    expect(dashboard.planVersionId).toBe("server-plan-version");
    expect(request).toHaveBeenCalledOnce();
  });
});

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "real-today-r2-client-request",
      generatedAt: "2026-07-15T00:00:00Z",
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
