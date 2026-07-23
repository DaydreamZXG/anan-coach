import { navigateSmart } from "../../utils/route-map";

const GROUPS = [
  {
    title: "我的备考",
    items: [
      { key: "goal", label: "目标与岗位", desc: "税务局一线岗 · 2026 国考", url: "/pages/goal/index" },
      { key: "plan", label: "学习偏好", desc: "每天 90 分钟 · 侧重行测", url: "/pages/study-preference/index" },
      { key: "history", label: "计划历史", desc: "看看走过的每一天", url: "/pages/plan-history/index" },
    ],
  },
  {
    title: "我的知识卡",
    items: [
      { key: "published", label: "我发布的", desc: "分享给考友的方法卡", url: "/pages/method-cards/index?tab=mine" },
      { key: "saved", label: "我收藏的", desc: "收藏后可被计划引用", url: "/pages/method-cards/index?tab=saved" },
    ],
  },
  {
    title: "设置",
    items: [
      { key: "notify", label: "通知设置", desc: "岸岸什么时候提醒你", url: "/pages/notification/index" },
      { key: "about", label: "关于", desc: "版本与帮助", url: "/pages/about/index" },
    ],
  },
];

Page({
  data: {
    groups: GROUPS,
    streakDays: 12,
    totalTasks: 86,
    joinedDays: 21,
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      const tabBar = this.getTabBar();
      if (tabBar) tabBar.setData({ active: "me" });
    }
  },

  onOpen(e: WechatMiniprogram.TouchEvent) {
    const { url } = e.currentTarget.dataset as { url: string };
    navigateSmart(url);
  },
});
