import { fetchEntitlement } from "../../api/endpoints";

type QuotaVM = {
  feature: string;
  title: string;
  line: string;
  unlimited: boolean;
};

Page({
  data: {
    loading: true,
    usingDemo: false,
    tierLabel: "",
    isPro: false,
    periodLine: "",
    quotas: [] as QuotaVM[],
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const ent = await fetchEntitlement();
      this.setData({
        loading: false,
        isPro: ent.tier === "pro",
        tierLabel: ent.tier === "pro" ? "Pro 会员" : "免费版",
        periodLine:
          ent.tier === "pro" && ent.subscriptionEnd
            ? `有效期至 ${ent.subscriptionEnd.slice(0, 10)}`
            : "按需升级，先把免费的用透",
        quotas: ent.featureQuotas.map((q) => ({
          feature: q.feature,
          title: q.title ?? q.feature,
          unlimited: q.accessMode === "unlimited",
          line:
            q.accessMode === "unlimited"
              ? "不限量"
              : `本${q.period === "day" ? "日" : "月"}剩 ${q.remaining ?? 0} / ${q.limit ?? 0} 次`,
        })),
      });
    } catch (err) {
      console.warn("[membership] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        isPro: false,
        tierLabel: "免费版",
        periodLine: "按需升级，先把免费的用透",
        quotas: [
          { feature: "ai_ask", title: "问岸岸", line: "本日剩 8 / 10 次", unlimited: false },
          { feature: "essay_review", title: "申论批改", line: "本月剩 2 / 3 次", unlimited: false },
          { feature: "mock_report", title: "模考报告", line: "不限量", unlimited: true },
        ],
      });
    }
  },

  onUpgrade() {
    // 微信虚拟支付链路后端已就绪，前端购买流程下个迭代接
    wx.showToast({ title: "购买通道快开了，先安心备考", icon: "none" });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
