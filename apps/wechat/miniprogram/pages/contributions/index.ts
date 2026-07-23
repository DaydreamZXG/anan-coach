import { fetchContributions, fetchRewardLedger } from "../../api/endpoints";

Page({
  data: {
    loading: true,
    usingDemo: false,
    cardCount: 0,
    rewardCount: 0,
    aiQuota: 0,
    membershipDays: 0,
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const [contrib, ledger] = await Promise.all([
        fetchContributions(),
        fetchRewardLedger(),
      ]);
      this.setData({
        loading: false,
        cardCount: contrib.cardIds.length,
        rewardCount: contrib.rewardEntryIds.length,
        aiQuota: ledger.aiQuotaBalance,
        membershipDays: Math.floor(ledger.membershipSecondsBalance / 86400),
      });
    } catch (err) {
      console.warn("[contributions] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        cardCount: 2,
        rewardCount: 5,
        aiQuota: 30,
        membershipDays: 3,
      });
    }
  },

  onGoPublish() {
    wx.navigateTo({
      url: "/pages/method-cards/publish",
      fail: () => wx.showToast({ title: "跳转失败，再点一次", icon: "none" }),
    });
  },

  onGoCards() {
    wx.navigateTo({
      url: "/pages/method-cards/index",
      fail: () => wx.showToast({ title: "跳转失败，再点一次", icon: "none" }),
    });
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
