import { afterEach, describe, expect, it, vi } from "vitest";
import { AbilityApi } from "../miniprogram/pages/positions/features/api/ability";
import { ApiClient } from "../miniprogram/api/client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AbilityApi", () => {
  it("loads only the authenticated current estimate", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/ability-estimates/current");
      expect(options.method).toBe("GET");
      options.success?.(
        success({
          estimate: null,
          readiness: "unavailable",
          missingComponents: ["xingce", "shenlun"],
        }),
      );
    });
    vi.stubGlobal("wx", { request });

    await new AbilityApi(new ApiClient("https://api.example.test", "token")).getCurrentEstimate();

    expect(request).toHaveBeenCalledOnce();
  });

  it("reports structured score components with an immutable command key", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/score-evidence/reported");
      expect(options.method).toBe("POST");
      expect(options.header).toMatchObject({ "Idempotency-Key": "score-command-1" });
      expect(options.data).toEqual({
        reportedContext: "full_mock",
        xingceScore: 65,
        shenlunScore: 55,
        timedStatus: "timed",
        occurredAt: "2026-07-12T04:00:00.000Z",
      });
      options.success?.(success({ evidence: {}, estimate: {} }));
    });
    vi.stubGlobal("wx", { request });
    const api = new AbilityApi(new ApiClient("https://api.example.test", "token"));

    await api.reportScore(
      {
        reportedContext: "full_mock",
        xingceScore: 65,
        shenlunScore: 55,
        timedStatus: "timed",
        occurredAt: "2026-07-12T04:00:00.000Z",
      },
      "score-command-1",
    );

    expect(request).toHaveBeenCalledOnce();
  });

  it("loads the authenticated score evidence history", async () => {
    const request = vi.fn((options: WechatMiniprogram.RequestOption) => {
      expect(options.url).toBe("https://api.example.test/api/v2/score-evidence");
      expect(options.method).toBe("GET");
      options.success?.(success({ items: [] }));
    });
    vi.stubGlobal("wx", { request });

    await new AbilityApi(
      new ApiClient("https://api.example.test", "token"),
    ).getScoreEvidenceHistory();

    expect(request).toHaveBeenCalledOnce();
  });
});

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "ability-request-1",
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
