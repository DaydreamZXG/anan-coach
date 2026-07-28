import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { ILinkChannelApi } from "../miniprogram/api/ilink-channel";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import captureTemplate from "../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import botSheetTemplate from "../miniprogram/components/ilink-bot-sheet/index.wxml?raw";

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
    await api.getHubBot();
    await api.startHubBot(false, "hub-start-command-1");
    await api.pollHubBot("authorization-1");
    await api.revokeHubBot("hub-revoke-command-1");

    expect(requests.map((item) => item.url)).toEqual([
      "https://api.example.test/api/v2/ilink-channel",
      "https://api.example.test/api/v2/ilink-channel/link-codes",
      "https://api.example.test/api/v2/ilink-channel/links/link-1/revoke",
      "https://api.example.test/api/v2/ilink-channel/bot",
      "https://api.example.test/api/v2/ilink-channel/bot/authorizations",
      "https://api.example.test/api/v2/ilink-channel/bot/authorizations/authorization-1",
      "https://api.example.test/api/v2/ilink-channel/bot",
    ]);
    expect(requests[1]?.header?.Authorization).toBe("Bearer access-token-1");
    expect(requests[1]?.header?.["Idempotency-Key"]).toBe("ilink-code-command-1");
    expect(requests[4]?.header?.["Idempotency-Key"]).toBe("hub-start-command-1");
    expect(requests[5]?.timeout).toBe(50_000);
    expect(requests[6]?.method).toBe("DELETE");
    expect(requests[6]?.header?.["Idempotency-Key"]).toBe("hub-revoke-command-1");
  });

  it("renders every bot authorization state and keeps the evidence boundary", () => {
    for (const token of [
      "status === 'loading'",
      "status === 'error'",
      "微信里的岸岸",
      "view.state === 'unbound'",
      "view.state === 'revoked'",
      "view.state === 'waiting_scan'",
      "view.state === 'scanned'",
      "view.state === 'connected'",
      "view.state === 'reauth_required'",
      "view.qrImageDataUrl",
      "view.statusCopy",
      "startAuthorization",
      "replaceAuthorization",
      "pollAuthorization",
      "revoke",
    ]) {
      expect(botSheetTemplate).toContain(token);
    }
    expect(botSheetTemplate).not.toContain("所需服务暂时不可用");
    expect(captureTemplate).toContain("<ilink-bot-sheet");
    expect(captureTemplate).toContain("不进入公共题库");
    expect(captureTemplate).toContain("不用于训练基础模型");
  });
});
