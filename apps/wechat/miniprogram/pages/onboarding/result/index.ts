import { acknowledgeOnboarding, fetchOnboardingProgress } from "../../../api/endpoints";
import { markOnboardingComplete } from "../../../api/auth";

Page({
  data: {
    statusBarHeight: 20,
    steps: [1, 2, 3, 4, 5, 6],
    step: 6,
    submitting: false,
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
  },

  async onEnter() {
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    try {
      // 确认完成用最新 progressVersion
      const progress = await fetchOnboardingProgress();
      await acknowledgeOnboarding(progress.progressVersion);
      markOnboardingComplete();
      wx.switchTab({ url: "/pages/today/index" });
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
      this.setData({ submitting: false });
    }
  },
});
