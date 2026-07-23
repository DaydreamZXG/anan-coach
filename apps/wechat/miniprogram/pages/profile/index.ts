interface ProfileEntry {
  id: string;
  name: string;
  meta?: string;
}

/** 条目 id → 设置子页路由 */
const ENTRY_ROUTES: Record<string, string> = {
  workspace: "/pages/settings/workspace/index",
  qualification: "/pages/settings/qualification/index",
  budget: "/pages/settings/study-budget/index",
  notifications: "/pages/settings/notifications/index",
  privacy: "/pages/settings/privacy/index",
  about: "/pages/settings/about/index",
};

Page({
  data: {
    statusBarHeight: 20,
    groups: [
      {
        title: "备考",
        items: [
          { id: "workspace", name: "岗位工作区", meta: "目标与分差" },
          { id: "qualification", name: "资格资料" },
          { id: "budget", name: "时间预算" },
        ] as ProfileEntry[],
      },
      {
        title: "系统",
        items: [
          { id: "notifications", name: "通知设置" },
          { id: "privacy", name: "隐私与数据导出" },
          { id: "about", name: "关于岸岸" },
        ] as ProfileEntry[],
      },
    ],
  },

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
  },

  onShow() {
    this.getTabBar()?.setData({ selected: 3 });
  },

  onEntry(e: WechatMiniprogram.TouchEvent) {
    const id = e.currentTarget.dataset.id as string;
    const url = ENTRY_ROUTES[id];
    if (!url) return;
    wx.navigateTo({ url });
  },
});
