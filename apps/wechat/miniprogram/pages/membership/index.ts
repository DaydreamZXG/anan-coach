import { ApiClient, ApiClientError } from "../../api/client";
import {
  CommerceApi,
  currentWechatPaymentPlatform,
  freshWechatLoginCode,
  requestWechatVirtualPayment,
  VirtualPaymentInvocationError,
  type VirtualPaymentOrderView,
  type WechatClientPlatform,
} from "../../api/commerce";
import type { CommerceDashboard, WechatOffer } from "../../api/generated/types.gen";
import { currentAccessToken } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  presentCommercialPolicies,
  type CommercialPolicyView,
} from "../../features/commerce/policy-presentation";

type OfferView = WechatOffer & { amountLabel: string; remainingLabel: string };

function commandKey(prefix: string): string {
  return `${prefix}-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

function amountLabel(minor: number): string {
  return `¥${(minor / 100).toFixed(2)}`;
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function isSettled(order: VirtualPaymentOrderView): boolean {
  return ["paid", "partially_refunded", "refund_processing", "refunded", "closed"].includes(
    order.state,
  );
}

async function reconcileUntilSettled(
  api: CommerceApi,
  orderId: string,
): Promise<VirtualPaymentOrderView> {
  let latest = (await api.reconcileVirtualPayment(orderId)).data;
  for (const wait of [700, 1400]) {
    if (isSettled(latest)) return latest;
    await delay(wait);
    latest = (await api.reconcileVirtualPayment(orderId)).data;
  }
  return latest;
}

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    dashboard: null as CommerceDashboard | null,
    offer: null as OfferView | null,
    policies: [] as CommercialPolicyView[],
    refundPolicyTitle: "退款规则",
    refundPolicyLabel: "",
    accepted: false,
    paying: false,
    refunding: false,
    entitlementLabel: "免费版",
    entitlementPeriod: "",
    effectiveDays: 0,
    effectiveDaysRequired: 0,
    weeklyReflections: 0,
    weeklyReflectionsRequired: 0,
    coreRatioLabel: "尚无到期任务",
    clientPlatform: "",
    paymentDisclosure: "微信虚拟支付 · 单次购买 · 不自动续费",
    paymentEnabled: environment.virtualPaymentEnabled,
  },

  onShow() {
    void this.loadDashboard();
  },

  async loadDashboard(recoverPending = true) {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const api = new CommerceApi(new ApiClient(undefined, token));
      const response = await api.dashboard();
      const dashboard = response.data;
      if (recoverPending) {
        const pending = dashboard.orders.find((item) => {
          const order = item as typeof item & { paymentChannel?: string };
          return (
            order.paymentChannel === "wechat_virtual_payment" &&
            ["creating", "awaiting_payment", "payment_uncertain"].includes(item.state)
          );
        });
        if (pending) {
          try {
            await api.reconcileVirtualPayment(pending.orderId);
            await this.loadDashboard(false);
            return;
          } catch {
            // The visible dashboard remains authoritative while provider reconciliation retries.
          }
        }
      }
      let platform: WechatClientPlatform | "" = "";
      try {
        platform = currentWechatPaymentPlatform();
      } catch {
        platform = "";
      }
      const rawOffer = dashboard.catalog.offers[0] ?? null;
      const policyViews = rawOffer ? presentCommercialPolicies(rawOffer.policies) : [];
      const refundPolicy = policyViews.find((item) => item.policyKind === "refund");
      const offer = rawOffer
        ? {
            ...rawOffer,
            amountLabel: amountLabel(rawOffer.amountMinor),
            remainingLabel:
              rawOffer.cohortRemaining === null
                ? "持续开放"
                : `剩余 ${String(rawOffer.cohortRemaining)} 个名额`,
          }
        : null;
      const ratio = dashboard.rewardProgress?.coreCompletionRatio;
      this.setData({
        status: "ready",
        dashboard,
        offer,
        policies: policyViews,
        refundPolicyTitle: refundPolicy?.title ?? "退款规则",
        refundPolicyLabel: refundPolicy?.contentLabel ?? "",
        entitlementLabel: dashboard.entitlement.tier === "pro" ? "陪练会员" : "免费版",
        entitlementPeriod: dashboard.entitlement.subscriptionEnd
          ? `有效至 ${dashboard.entitlement.subscriptionEnd.slice(0, 10)}`
          : "当前无付费权益",
        effectiveDays: dashboard.rewardProgress?.effectiveDays ?? 0,
        effectiveDaysRequired: dashboard.rewardProgress?.effectiveDaysRequired ?? 0,
        weeklyReflections: dashboard.rewardProgress?.weeklyReflections ?? 0,
        weeklyReflectionsRequired: dashboard.rewardProgress?.weeklyReflectionsRequired ?? 0,
        coreRatioLabel:
          ratio === null || ratio === undefined
            ? "尚无到期任务"
            : `${String(Math.round(ratio * 100))}%`,
        clientPlatform: platform,
        paymentDisclosure: !environment.virtualPaymentEnabled
          ? "会员购买暂未开放，当前不会创建支付订单"
          : platform === "ios"
            ? "iOS 由 Apple 收款 · 退款需在 App Store 申请 · 最终结果由 Apple 决定"
            : "微信虚拟支付 · 单次购买 · 不自动续费",
      });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "会员信息加载失败，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  retry() {
    void this.loadDashboard();
  },

  toggleAccepted() {
    this.setData({ accepted: !this.data.accepted });
  },

  async buy() {
    if (!environment.virtualPaymentEnabled) {
      void wx.showToast({ title: "会员购买暂未开放", icon: "none" });
      return;
    }
    const token = currentAccessToken();
    const offer = this.data.offer;
    if (!token || !offer || this.data.paying) return;
    if (!this.data.accepted) {
      void wx.showToast({ title: "请先确认四项规则", icon: "none" });
      return;
    }
    if (offer.recruitmentState !== "open" && offer.recruitmentState !== "review_required") {
      void wx.showToast({ title: "当前暂不开放购买", icon: "none" });
      return;
    }
    const byKind = new Map(offer.policies.map((item) => [item.policyKind, item.policyVersion]));
    this.setData({ paying: true, errorMessage: "" });
    try {
      const api = new CommerceApi(new ApiClient(undefined, token));
      const platform = currentWechatPaymentPlatform();
      const purchaseCommandKey = commandKey("founder-virtual-order");
      const paymentLoginCode = await freshWechatLoginCode();
      const created = await api.createVirtualPaymentOrder({
        commandKey: purchaseCommandKey,
        offerVersionId: offer.offerVersionId,
        expectedAmountMinor: offer.amountMinor,
        acceptedPolicies: {
          serviceTermsVersion: byKind.get("service_terms") ?? "",
          refundPolicyVersion: byKind.get("refund") ?? "",
          rewardPolicyVersion: byKind.get("process_reward") ?? "",
          marketingPolicyVersion: byKind.get("marketing") ?? "",
        },
        paymentLoginCode,
        clientPlatform: platform,
      });
      const order = created.data.order;
      if (isSettled(order)) {
        if (order.state === "closed" || order.state === "refunded") {
          throw new Error("这笔订单已关闭，未产生新的会员权益。请重新下单。");
        }
        await this.loadDashboard(false);
        return;
      }
      if (!order.virtualPayment) throw new Error("虚拟支付签名参数缺失");
      let invocationError: unknown = null;
      try {
        await requestWechatVirtualPayment(order.virtualPayment);
      } catch (error) {
        invocationError = error;
      }
      const reconciled = await reconcileUntilSettled(api, order.orderId);
      if (!["paid", "partially_refunded", "refund_processing"].includes(reconciled.state)) {
        if (invocationError instanceof VirtualPaymentInvocationError && invocationError.canceled) {
          throw new Error("支付已取消，订单没有到账。");
        }
        throw new Error("支付结果仍在核对，请稍后回到本页自动恢复订单。");
      }
      await this.loadDashboard();
      void wx.showToast({ title: "支付已核验", icon: "success" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      const message =
        apiError?.message ??
        (error instanceof Error ? error.message : "支付结果未确认，请刷新订单状态。");
      await this.loadDashboard(false);
      this.setData({ errorMessage: message });
    } finally {
      this.setData({ paying: false });
    }
  },

  requestRefund() {
    const eligibility = this.data.dashboard?.refundEligibility;
    const order = this.data.dashboard?.orders.find((item) => item.paidAt !== null);
    if (!eligibility?.eligible || !order || this.data.refunding) return;
    wx.showModal({
      title: "确认申请退款？",
      content:
        this.data.clientPlatform === "ios"
          ? `这笔 ${amountLabel(eligibility.remainingRefundableMinor)} 的 iOS 订单需在 App Store 提交退款；当前订单已通过服务端退款资格校验，最终结果由 Apple 决定。`
          : `将按微信虚拟支付路径申请退回 ${amountLabel(eligibility.remainingRefundableMinor)}，无需提交成绩证明。`,
      confirmText: "申请退款",
      confirmColor: "#C83F56",
      success: (result) => {
        if (result.confirm) void this.refund(order.orderId);
      },
    });
  },

  async refund(orderId: string) {
    const token = currentAccessToken();
    if (!token) return;
    this.setData({ refunding: true, errorMessage: "" });
    try {
      const result = await new CommerceApi(new ApiClient(undefined, token)).refundVirtualPayment(
        orderId,
        commandKey("not-suitable-refund"),
      );
      await this.loadDashboard();
      const action = result.data as typeof result.data & {
        providerAction?: "none" | "app_store_required";
        providerActionUrl?: string | null;
        providerActionMessage?: string | null;
      };
      if (action.providerAction === "app_store_required") {
        wx.showModal({
          title: "请在 App Store 申请退款",
          content:
            action.providerActionMessage ??
            "打开 reportaproblem.apple.com，登录购买所用 Apple 账户并选择这笔订单。",
          confirmText: "复制网址",
          cancelText: "知道了",
          success: (modal) => {
            if (modal.confirm && action.providerActionUrl) {
              void wx.setClipboardData({ data: action.providerActionUrl });
            }
          },
        });
      } else {
        void wx.showToast({ title: "退款已受理", icon: "success" });
      }
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({ errorMessage: apiError?.message ?? "退款申请失败，请重试。" });
    } finally {
      this.setData({ refunding: false });
    }
  },
});
