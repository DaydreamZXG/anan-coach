import { fetchTodayTaskstream } from "../../api/endpoints";
import type { TodayTaskstream } from "../../api/generated/types.gen";

const TRACK_LABEL: Record<string, string> = {
  xingce: "行测",
  essay: "申论",
  recovery: "复盘",
  mock: "模考",
};

/** 后端不可达时的演示数据，保证首屏永远有内容可看 */
const DEMO: TodayTaskstream = {
  date: "今天",
  headline: "今天先补言语，再稳住申论手感",
  planBasis: "依据你近 7 天的做题表现与目标分差",
  rationale:
    "你的言语理解最近三次正确率掉到 62%，是目前离目标分最近、提升最快的一块。所以我把它排在第一件事，趁状态好的时候啃。申论放在后面做轻量保温，别断手感就行。",
  primaryAction: { title: "开始今日第一件事", subtitle: "言语理解 · 20 题" },
  target: {
    exam: "2026 国考",
    positionGroup: "税务局一线岗",
    scoreGap: 6.5,
    scoreGapAvailable: true,
  },
  calibration: {
    confidence: "中",
    statusLabel: "估分渐稳",
    dataLine: "行测约 68 分 · 还差一次限时模考可收窄区间",
    xingceScore: 68,
  },
  trackMix: [
    { track: "xingce", minutes: 45, reason: "主攻言语" },
    { track: "essay", minutes: 25, reason: "保温手感" },
  ],
  completedTaskIds: [],
  alerts: [],
  tasks: [
    {
      id: "demo-1",
      title: "言语理解 · 20 题",
      subtitle: "限时 25 分钟",
      reason: "近三次正确率 62%，是当前提分性价比最高的模块",
      sourceGrounding: "基于你的错题分布与目标分差",
      estimatedMinutes: 25,
      track: "xingce",
      kind: "practice",
      startUrl: "/pages/practice/index?taskId=demo-1",
    },
    {
      id: "demo-2",
      title: "昨日错题复盘 · 8 题",
      subtitle: "趁记忆还热",
      reason: "昨天有 8 道题做错但未复盘，今天回收才不浪费",
      sourceGrounding: "来自昨日练习的待复盘队列",
      estimatedMinutes: 15,
      track: "recovery",
      kind: "review",
      startUrl: "/pages/review/index",
    },
    {
      id: "demo-3",
      title: "申论素材阅读 · 1 篇",
      subtitle: "轻量保温",
      reason: "保持申论手感，避免长时间不碰生疏",
      sourceGrounding: "双轨平衡：申论今日已连续 2 天未练",
      estimatedMinutes: 20,
      track: "essay",
      kind: "essay",
      startUrl: "/pages/essay-material/index",
    },
  ],
};

Page({
  data: {
    loading: true,
    usingDemo: false,
    stream: null as TodayTaskstream | null,
    tasksView: [] as Array<
      TodayTaskstream["tasks"][number] & {
        trackLabel: string;
        done: boolean;
        order: number;
      }
    >,
    doneCount: 0,
    totalCount: 0,
    allDone: false,
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      const tabBar = this.getTabBar();
      if (tabBar) tabBar.setData({ active: "today" });
    }
    this.load();
  },

  async load() {
    this.setData({ loading: true });
    try {
      const stream = await fetchTodayTaskstream();
      this.render(stream, false);
    } catch (err) {
      console.error("[today] taskstream failed, fallback to demo", err);
      this.render(DEMO, true);
    }
  },

  render(stream: TodayTaskstream, usingDemo: boolean) {
    const doneSet = new Set(stream.completedTaskIds || []);
    const tasksView = stream.tasks.map((t, i) => ({
      ...t,
      trackLabel: TRACK_LABEL[t.track] || t.track,
      done: doneSet.has(t.id),
      order: i + 1,
    }));
    const doneCount = tasksView.filter((t) => t.done).length;
    this.setData({
      loading: false,
      usingDemo,
      stream,
      tasksView,
      doneCount,
      totalCount: tasksView.length,
      allDone: tasksView.length > 0 && doneCount === tasksView.length,
    });
  },

  onStartTask(e: WechatMiniprogram.TouchEvent) {
    const { url } = e.currentTarget.dataset as { url: string };
    if (!url) return;
    wx.navigateTo({
      url,
      fail: () => wx.showToast({ title: "该功能开发中", icon: "none" }),
    });
  },

  onOpenSettlement() {
    wx.navigateTo({
      url: "/pages/daily-settlement/index",
      fail: () => wx.showToast({ title: "结算页开发中", icon: "none" }),
    });
  },

  onAskAnan() {
    wx.switchTab({ url: "/pages/anan/index" });
  },

  onTapAlert(e: WechatMiniprogram.TouchEvent) {
    const { url } = e.currentTarget.dataset as { url: string };
    if (!url) return;
    wx.navigateTo({
      url,
      fail: () => wx.showToast({ title: "开发中", icon: "none" }),
    });
  },
});
