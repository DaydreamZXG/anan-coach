import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { ILinkChannelApi } from "../miniprogram/api/ilink-channel";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import captureTemplate from "../miniprogram/pages/capture-import/index.wxml?raw";

describe("official iLink photo channel", () => {
  it("uses authenticated idempotent account-bound endpoints", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("wx", {
      request: (options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        options.success?.({
          data: {
            data: {
              linkState: "pending",
              linkId: "link-1",
              bindingCode: "ABC234DEF567",
              expiresAt: "2026-07-21T04:10:00Z",
              queryCount: 0,
              collectionCount: 0,
              usageCopy: "usage",
              evidenceCopy: "evidence",
            },
            requestId: "ilink-1",
            generatedAt: "2026-07-21T04:00:00Z",
          },
          statusCode: 200,
          header: {},
          cookies: [],
          exception: { reasons: [], retryCount: 0 },
          profile: {} as WechatMiniprogram.RequestProfile,
          useHttpDNS: false,
          errMsg: "request:ok",
        });
      },
    });
    const api = new ILinkChannelApi(new ApiClient("https://api.example.test", "access-token-1"));
    await api.get();
    await api.issue(false, "ilink-code-command-1");
    await api.revoke("link-1", "ilink-revoke-command-1");

    expect(requests.map((item) => item.url)).toEqual([
      "https://api.example.test/api/v2/ilink-channel",
      "https://api.example.test/api/v2/ilink-channel/link-codes",
      "https://api.example.test/api/v2/ilink-channel/links/link-1/revoke",
    ]);
    expect(requests[1]?.header?.Authorization).toBe("Bearer access-token-1");
    expect(requests[1]?.header?.["Idempotency-Key"]).toBe("ilink-code-command-1");
  });

  it("renders every truthful link state and the evidence boundary", () => {
    for (const token of [
      "channelStatus === 'loading'",
      "channelStatus === 'unavailable'",
      "channelStatus === 'error'",
      "小程序拍照搜题",
      "channelView.linkState === 'unbound'",
      "channelView.linkState === 'revoked'",
      "channelView.linkState === 'pending'",
      "channelView.linkState === 'bound'",
      "channelExpired",
      "channelView.evidenceCopy",
      "issueChannelCode",
      "replaceChannelCode",
      "copyChannelCode",
      "revokeChannelLink",
    ]) {
      expect(captureTemplate).toContain(token);
    }
    expect(captureTemplate).toContain("不进入公共题库或模型训练");
  });
});
