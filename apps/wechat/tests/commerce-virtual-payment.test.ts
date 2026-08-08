import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import {
  CommerceApi,
  freshWechatLoginCode,
  requestWechatVirtualPayment,
} from "../miniprogram/account/shared/api/commerce";
import type { VirtualPaymentInvocationError } from "../miniprogram/account/shared/api/commerce";
import type { CommercialPolicySnapshot } from "../miniprogram/api/generated/types.gen";
import { presentCommercialPolicies } from "../miniprogram/account/shared/features/commerce/policy-presentation";

// @ts-expect-error Vite exposes TypeScript source through its raw asset loader.
import commerceSource from "../miniprogram/account/shared/api/commerce.ts?raw";
// @ts-expect-error Vite exposes TypeScript source through its raw asset loader.
import membershipSource from "../miniprogram/account/membership/index.ts?raw";
// @ts-expect-error Vite exposes WXML source through its raw asset loader.
import membershipTemplate from "../miniprogram/account/membership/index.wxml?raw";
// @ts-expect-error Vite exposes WXML source through its raw asset loader.
import meTemplate from "../miniprogram/pages/me/index.wxml?raw";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WeChat virtual payment", () => {
  it("uses only the virtual-payment server commands", async () => {
    const calls: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("wx", {
      request: vi.fn((options: WechatMiniprogram.RequestOption) => {
        calls.push(options);
        options.success?.(success({}));
      }),
    });
    const api = new CommerceApi(new ApiClient("https://api.example.test", "token"));

    await api.createVirtualPaymentOrder({
      commandKey: "virtual-order-command-1",
      offerVersionId: "36000000-0000-0000-0000-000000000001",
      expectedAmountMinor: 6900,
      acceptedPolicies: {
        serviceTermsVersion: "founder-service-terms@v1",
        refundPolicyVersion: "founder-refund@v2",
        rewardPolicyVersion: "founder-process-reward@v1",
        marketingPolicyVersion: "founder-marketing@v1",
      },
      paymentLoginCode: "fresh-login-code",
      clientPlatform: "ios",
    });
    await api.reconcileVirtualPayment("order/id");
    await api.refundVirtualPayment("order/id", "virtual-refund-command-1");

    expect(calls.map((call) => call.url)).toEqual([
      "https://api.example.test/api/v2/commerce/virtual-payment/orders",
      "https://api.example.test/api/v2/commerce/virtual-payment/orders/order%2Fid/reconcile",
      "https://api.example.test/api/v2/commerce/virtual-payment/orders/order%2Fid/refunds",
    ]);
    expect(calls[0]?.data).toMatchObject({
      expectedAmountMinor: 6900,
      paymentLoginCode: "fresh-login-code",
      clientPlatform: "ios",
    });
  });

  it("passes the exact signed JSON string to wx.requestVirtualPayment", async () => {
    const requestVirtualPayment = vi.fn(
      (options: WechatMiniprogram.RequestVirtualPaymentOption) => {
        options.success?.({ errMsg: "requestVirtualPayment:ok" });
      },
    );
    vi.stubGlobal("wx", {
      canIUse: vi.fn(() => true),
      requestVirtualPayment,
    });
    const signData =
      '{"offerId":"offer-1","buyQuantity":1,"goodsPrice":6900,"outTradeNo":"A8ORDER1"}';

    await requestWechatVirtualPayment({
      signData,
      paySig: "a".repeat(64),
      signature: "b".repeat(64),
      mode: "short_series_goods",
    });

    expect(requestVirtualPayment).toHaveBeenCalledWith(
      expect.objectContaining({
        signData,
        paySig: "a".repeat(64),
        signature: "b".repeat(64),
        mode: "short_series_goods",
      }),
    );
  });

  it("uses a fresh wx.login code and preserves user cancellation", async () => {
    vi.stubGlobal("wx", {
      login: vi.fn((options: WechatMiniprogram.LoginOption) => {
        options.success?.({ code: "new-code", errMsg: "login:ok" });
      }),
      canIUse: vi.fn(() => true),
      requestVirtualPayment: vi.fn((options: WechatMiniprogram.RequestVirtualPaymentOption) => {
        options.fail?.({
          errCode: -2,
          errMsg: "requestVirtualPayment:fail cancel",
        });
      }),
    });

    await expect(freshWechatLoginCode()).resolves.toBe("new-code");
    await expect(
      requestWechatVirtualPayment({
        signData: "{}",
        paySig: "a".repeat(64),
        signature: "b".repeat(64),
        mode: "short_series_goods",
      }),
    ).rejects.toMatchObject({
      errorCode: -2,
      canceled: true,
    } satisfies Partial<VirtualPaymentInvocationError>);
  });

  it("contains no retired wx.requestPayment purchase path", () => {
    expect(commerceSource).toContain("wx.requestVirtualPayment");
    expect(commerceSource).not.toContain("wx.requestPayment");
    expect(membershipSource).toContain("freshWechatLoginCode");
    expect(membershipSource).toContain("reconcileUntilSettled");
    expect(membershipSource).not.toContain("requestWechatPayment");
  });

  it("opens a public-beta explanation without exposing a purchase action", () => {
    expect(meTemplate).toContain("openMembership");
    expect(meTemplate).toContain("当前免费");
    expect(membershipTemplate).toContain("当前不需要开通会员");
    expect(membershipTemplate).toContain("公测期免费使用");
    expect(membershipTemplate).toContain("7 折创始价");
    expect(membershipTemplate).not.toContain("冷启动");
    expect(membershipTemplate).not.toContain("全功能免费");
    expect(membershipTemplate).toContain('wx:elif="{{freeColdStart}}"');
    expect(membershipTemplate).toContain('wx:if="{{offer && paymentEnabled}}"');
  });

  it("renders purchase promises only from the versioned server policy content", () => {
    const policies = policyFixtures();
    const views = presentCommercialPolicies(policies);
    expect(views).toHaveLength(4);
    expect(views.find((item) => item.policyKind === "service_terms")?.contentLabel).toContain(
      "45 天",
    );
    expect(views.find((item) => item.policyKind === "refund")?.contentLabel).toContain(
      "14 个自然日",
    );
    expect(views.find((item) => item.policyKind === "process_reward")?.contentLabel).toContain(
      "12 个有效学习日",
    );
    expect(() => presentCommercialPolicies(policies.slice(0, 3))).toThrow("四项固化政策");
  });
});

function policyFixtures(): CommercialPolicySnapshot[] {
  return [
    policy("service_terms", {
      serviceDays: 45,
      automaticRenewal: false,
    }),
    policy("refund", {
      windowDays: 14,
      scoreEvidenceRequired: false,
      interviewRequired: false,
      completionRateRequired: false,
    }),
    policy("process_reward", {
      effectiveDay: { requiredCount: 12, rewardServiceDays: 6 },
      weeklyReflection: { requiredCount: 3, rewardMinorUnits: 1800 },
      coreCompletion: { requiredRatio: 0.75 },
    }),
    policy("marketing", {
      prohibited: ["不提分全退", "每提 1 分返现"],
    }),
  ];
}

function policy(
  policyKind: CommercialPolicySnapshot["policyKind"],
  content: Record<string, unknown>,
): CommercialPolicySnapshot {
  return {
    policyKind,
    policyVersion: `${policyKind}@test`,
    title: policyKind,
    content,
    contentSha256: "a".repeat(64),
  };
}

function success(data: unknown): WechatMiniprogram.RequestSuccessCallbackResult {
  return {
    data: {
      data,
      requestId: "virtual-payment-request-1",
      generatedAt: "2026-07-17T00:00:00Z",
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
