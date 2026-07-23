interface StepItem {
  no: string;
  name: string;
  desc: string;
}

Page({
  data: {
    statusBarHeight: 20,
    steps: [
      { no: "01", name: "说清你的情况", desc: "资格与经历" },
      { no: "02", name: "选定目标岗位", desc: "拿到目标分" },
      { no: "03", name: "10 分钟定位测试", desc: "拿到起跑线" },
    ] as StepItem[],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
  },

  onStart() {
    wx.redirectTo({ url: "/pages/onboarding/experience/index" });
  },
});
