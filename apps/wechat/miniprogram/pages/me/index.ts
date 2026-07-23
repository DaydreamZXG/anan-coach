import { navigateSmart } from "../../utils/route-map";

const GROUPS = [
  {
    title: "我的备考",
    items: [
      { key: "goal", label: "目标与岗位", desc: "税务局一线岗 · 2026 国考", url: "/pages/goal/index" },
      { key: "plan", label: "学习偏好", desc: "每天 90 分钟 · 侧重行测", url: "/pages/study-preference/index" },
      { key: "week", label: "本周节奏", desc: "五天训练一天模考一天休整", url: "/pages/weekly-plan/index" },
      { key: "history", label: "计划历史", desc: "看看走过的每一天", url: "/pages/plan-history/index" },
      { key: "score", label: "补一笔成绩", desc: "考过的分数都算数", url: "/pages/score-import/index" },
      { key: "qualification", label: "报考资格核对", desc: "硬条件齐了岗位才判得准", url: "/pages/qualification/index" },
    ],
  },
  {
    title: "我的知识卡",
    items: [
      { key: "published", label: "我发布的", desc: "分享给考友的方法卡", url: "/pages/method-cards/index?tab=mine" },
      { key: "saved", label: "我收藏的", desc: "收藏后可被计划引用", url: "/pages/method-cards/index?tab=saved" },
      { key: "contrib", label: "我的贡献", desc: "贡献换的 AI 额度和会员", url: "/pages/contributions/index" },
    ],
  },
  {
    title: "设置",
    items: [
      { key: "membership", label: "会员与额度", desc: "各功能额度一目了然", url: "/pages/membership/index" },
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
