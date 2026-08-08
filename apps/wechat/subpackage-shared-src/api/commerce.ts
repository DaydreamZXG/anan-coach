import type { ApiEnvelope } from "../../miniprogram/domain/contracts";
import type {
  CommerceDashboard,
  WechatOrderView,
  WechatRefundResponse,
} from "../../miniprogram/api/generated/types.gen";
import type { ApiClient } from "../../miniprogram/api/client";
import {
  resolveWechatClientPlatform,
  supportsVirtualPayment,
  type WechatClientPlatform,
} from "../platform/payment-platform";

export type { WechatClientPlatform } from "../platform/payment-platform";

export interface WechatVirtualPaymentParameters {
  signData: string;
  paySig: string;
  signature: string;
  mode: "short_series_goods";
}

export type VirtualPaymentOrderView = WechatOrderView & {
  paymentChannel: "wechat_virtual_payment";
  clientPlatform: WechatClientPlatform;
  virtualPayment: WechatVirtualPaymentParameters | null;
};

export interface VirtualPaymentOrderCreateResponse {
  order: VirtualPaymentOrderView;
}

export interface PolicyAcceptancesInput {
  serviceTermsVersion: string;
  refundPolicyVersion: string;
  rewardPolicyVersion: string;
  marketingPolicyVersion: string;
}

export class CommerceApi {
  constructor(private readonly client: ApiClient) {}

  dashboard(): Promise<ApiEnvelope<CommerceDashboard>> {
    return this.client.get<CommerceDashboard>("/api/v2/commerce/dashboard");
  }

  createVirtualPaymentOrder(input: {
    commandKey: string;
    offerVersionId: string;
    expectedAmountMinor: number;
    acceptedPolicies: PolicyAcceptancesInput;
    paymentLoginCode: string;
    clientPlatform: WechatClientPlatform;
  }): Promise<ApiEnvelope<VirtualPaymentOrderCreateResponse>> {
    return this.client.post<VirtualPaymentOrderCreateResponse>(
      "/api/v2/commerce/virtual-payment/orders",
      input,
    );
  }

  reconcileVirtualPayment(orderId: string): Promise<ApiEnvelope<VirtualPaymentOrderView>> {
    return this.client.post<VirtualPaymentOrderView>(
      `/api/v2/commerce/virtual-payment/orders/${encodeURIComponent(orderId)}/reconcile`,
      {},
    );
  }

  refundVirtualPayment(
    orderId: string,
    commandKey: string,
  ): Promise<ApiEnvelope<WechatRefundResponse>> {
    return this.client.post<WechatRefundResponse>(
      `/api/v2/commerce/virtual-payment/orders/${encodeURIComponent(orderId)}/refunds`,
      { commandKey, reason: "not_suitable" },
    );
  }
}

export class VirtualPaymentInvocationError extends Error {
  constructor(
    message: string,
    readonly errorCode: number | null,
    readonly canceled: boolean,
  ) {
    super(message);
    this.name = "VirtualPaymentInvocationError";
  }
}

export function freshWechatLoginCode(): Promise<string> {
  return new Promise((resolve, reject) => {
    wx.login({
      success: (result) => {
        if (result.code) resolve(result.code);
        else reject(new Error("微信支付登录凭证为空"));
      },
      fail: () => {
        reject(new Error("无法刷新微信支付登录凭证"));
      },
    });
  });
}

export function currentWechatPaymentPlatform(): WechatClientPlatform {
  const platform = resolveWechatClientPlatform();
  if (platform) return platform;
  throw new Error("当前设备不支持微信虚拟支付，请在受支持的微信客户端内购买。 ");
}

export function requestWechatVirtualPayment(
  payment: WechatVirtualPaymentParameters,
): Promise<void> {
  if (!supportsVirtualPayment()) {
    return Promise.reject(
      new VirtualPaymentInvocationError(
        "当前微信版本不支持虚拟支付，请升级微信后重试。",
        null,
        false,
      ),
    );
  }
  return new Promise((resolve, reject) => {
    wx.requestVirtualPayment({
      // The official runtime contract requires a JSON string; the bundled typings
      // incorrectly expose the already-stringified payload as an object shape.
      signData: payment.signData as unknown as WechatMiniprogram.SignData,
      paySig: payment.paySig,
      signature: payment.signature,
      mode: payment.mode,
      success: () => {
        resolve();
      },
      fail: (result) => {
        const errorCode = typeof result.errCode === "number" ? result.errCode : null;
        reject(
          new VirtualPaymentInvocationError(
            result.errMsg || "虚拟支付未完成",
            errorCode,
            errorCode === -2,
          ),
        );
      },
    });
  });
}
