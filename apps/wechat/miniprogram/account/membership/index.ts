import { ApiClient, ApiClientError } from "../../api/client";
import {
  CommerceApi,
  currentWechatPaymentPlatform,
  freshWechatLoginCode,
  requestWechatVirtualPayment,
  VirtualPaymentInvocationError,
  type VirtualPaymentOrderView,
  type WechatClientPlatform,
} from "../shared/api/commerce";
import type { CommerceDashboard, WechatOffer } from "../../api/generated/types.gen";
import { currentAccessToken } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  presentCommercialPolicies,
  type CommercialPolicyView,
} from "../shared/features/commerce/policy-presentation";

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
    entitlementLabel: "当前服务",
    entitlementPeriod: "",
    effectiveDays: 0,
    effectiveDaysRequired: 0,
    weeklyReflections: 0,
    weeklyReflectionsRequired: 0,
    coreRatioLabel: "尚无到期任务",
    clientPlatform: "",
    freeColdStart: true,
    paymentDisclosure: "当前未开放购买，不会产生任何扣费。",
    paymentEnabled: false,
    commercialModeLabel: "当前未开放购买",
    refundBlockingLabel: "",
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
      const freeColdStart = dashboard.catalog.commercialMode === "free_cold_start";
      if (recoverPending && dashboard.catalog.orderingEnabled) {
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
      if (dashboard.catalog.orderingEnabled) {
        try {
          platform = currentWechatPaymentPlatform();
        } catch {
          platform = "";
        }
      }
      const rawOffer = dashboard.catalog.orderingEnabled
        ? (dashboard.catalog.offers[0] ?? null)
        : null;
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
        freeColdStart,
        offer,
        policies: policyViews,
        refundPolicyTitle: refundPolicy?.title ?? "退款规则",
        refundPolicyLabel: refundPolicy?.contentLabel ?? "",
        entitlementLabel: freeColdStart
          ? "当前服务"
          : dashboard.entitlement.tier === "pro"
            ? "陪练会员"
            : "免费版",
        entitlementPeriod: freeColdStart
          ? "注册后可使用"
          : dashboard.entitlement.subscriptionEnd
            ? `有效至 ${dashboard.entitlement.subscriptionEnd.slice(0, 10)}`
            : "当前没有开通会员",
        effectiveDays: dashboard.rewardProgress?.effectiveDays ?? 0,
        effectiveDaysRequired: dashboard.rewardProgress?.effectiveDaysRequired ?? 0,
        weeklyReflections: dashboard.rewardProgress?.weeklyReflections ?? 0,
        weeklyReflectionsRequired: dashboard.rewardProgress?.weeklyReflectionsRequired ?? 0,
        coreRatioLabel:
          ratio === null || ratio === undefined
            ? "尚无到期任务"
            : `${String(Math.round(ratio * 100))}%`,
        clientPlatform: platform,
        paymentEnabled: dashboard.catalog.orderingEnabled && environment.virtualPaymentEnabled,
        commercialModeLabel: freeColdStart ? "当前未开放购买" : "开通了哪些，就能用哪些",
        refundBlockingLabel: refundBlockingLabel(dashboard.refundEligibility?.blockingCode ?? null),
        paymentDisclosure: freeColdStart
          ? "当前未开放购买，不会产生任何扣费。"
          : !environment.virtualPaymentEnabled
            ? "现在还不能开通会员，也不会扣任何费用。"
            : "通过微信支付 · 一次付费 · 不自动续费",
      });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "会员信息没加载出来，再试一次吧。",
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
    const token = currentAccessToken();
    const offer = this.data.offer;
    if (!token || !offer || this.data.paying) return;
    if (!this.data.accepted) {
      void wx.showToast({ title: "先看完并确认这四项规则哦", icon: "none" });
      return;
    }
    if (offer.recruitmentState !== "open" && offer.recruitmentState !== "review_required") {
      void wx.showToast({ title: "现在还不能开通，稍后再来吧", icon: "none" });
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
          throw new Error("这笔已经关闭，没有产生新的会员，重新开通就好。");
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
          throw new Error("付款取消了，没有扣费。");
        }
        throw new Error("付款结果还在核对，稍后回到这一页就会自动恢复。");
      }
      await this.loadDashboard();
      void wx.showToast({ title: "付款已确认", icon: "success" });
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      const message =
        apiError?.message ??
        (error instanceof Error ? error.message : "付款结果还没确认，稍后刷新看看。");
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
      content: `会通过微信把 ${amountLabel(eligibility.remainingRefundableMinor)} 退回来，不用交成绩证明。`,
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
        providerAction?: string;
        providerActionUrl?: string | null;
        providerActionMessage?: string | null;
      };
      if (action.providerAction && action.providerAction !== "none") {
        wx.showModal({
          title: "退款需人工处理",
          content:
            action.providerActionMessage ?? "这笔是以前的订单，小程序里退不了，麻烦联系客服处理。",
          confirmText: "复制信息",
          cancelText: "知道了",
          success: (modal) => {
            if (modal.confirm && action.providerActionUrl) {
              void wx.setClipboardData({ data: action.providerActionUrl });
            }
          },
        });
      } else {
        void wx.showToast({ title: "退款申请已提交", icon: "success" });
      }
    } catch (error) {
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({ errorMessage: apiError?.message ?? "退款没申请上，再试一次吧。" });
    } finally {
      this.setData({ refunding: false });
    }
  },
});

function refundBlockingLabel(code: string | null): string {
  if (!code) return "这笔订单还不能退款";
  const labels: Record<string, string> = {
    order_not_paid: "这笔还没有付款",
    refund_window_expired: "已经过了可以退款的时间",
    refund_already_processing: "退款正在处理中",
    already_fully_refunded: "这笔已经全额退过了",
    order_state_ineligible: "这笔订单现在的状态还退不了",
  };
  return labels[code] ?? "这笔订单暂时还退不了";
}
