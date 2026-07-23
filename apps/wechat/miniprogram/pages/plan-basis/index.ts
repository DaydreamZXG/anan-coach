import { fetchPlanBasis, fetchTodayDashboard, setTodayCapacity } from "../../api/endpoints";

type BasisTaskVM = {
  taskId: string;
  order: number;
  title: string;
  reason: string;
  minutes: number;
  trackLabel: string;
};

const TRACK_LABEL: Record<string, string> = {
  xingce: "行测",
  essay: "申论",
  recovery: "复盘",
  mock: "模考",
};

const DEMO_TASKS: BasisTaskVM[] = [
  { taskId: "d1", order: 1, title: "言语理解 · 20 题", reason: "近三次正确率 62%，低于目标线 13 个点，是当前提分性价比最高的口子", minutes: 25, trackLabel: "行测" },
  { taskId: "d2", order: 2, title: "昨日错题复盘 · 8 题", reason: "昨天 8 道错题还没归因，今天不回收，同类题下次还会错", minutes: 15, trackLabel: "复盘" },
  { taskId: "d3", order: 3, title: "申论素材阅读 · 1 篇", reason: "申论已连续 2 天没碰，再空一天手感会掉", minutes: 20, trackLabel: "申论" },
];

const ENERGY_LEVELS = [
  { value: 1, label: "很疲惫" },
  { value: 2, label: "有点累" },
  { value: 3, label: "还行" },
  { value: 4, label: "状态好" },
  { value: 5, label: "满格" },
];

const MINUTE_STEPS = [30, 45, 60, 90, 120, 180, 240];

Page({
  data: {
    loading: true,
    usingDemo: false,
    // 依据
    headline: "",
    rationale: "",
    tasks: [] as BasisTaskVM[],
    // 调整
    capacityVersionId: "",
    energyLevels: ENERGY_LEVELS,
    minuteSteps: MINUTE_STEPS,
    energy: 3,
    minuteIndex: 3,
    adjusting: false,
    adjustOpen: false,
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const [basis, dashboard] = await Promise.all([
        fetchPlanBasis(),
        fetchTodayDashboard(),
      ]);
      const cap = dashboard.capacity;
      const nearest = MINUTE_STEPS.reduce(
        (best, m, i) =>
          Math.abs(m - cap.availableMinutes) <
          Math.abs((MINUTE_STEPS[best] ?? 0) - cap.availableMinutes)
            ? i
            : best,
        0,
      );
      this.setData({
        loading: false,
        headline: basis.headline,
        rationale: basis.rationale,
        tasks: basis.tasks
          .slice()
          .sort((a, b) => a.order - b.order)
          .map((t) => ({
            taskId: t.taskId,
            order: t.order,
            title: t.title,
            reason: t.reason,
            minutes: t.estimatedMinutes,
            trackLabel: TRACK_LABEL[t.track] ?? t.track,
          })),
        capacityVersionId: cap.capacityVersionId,
        energy: cap.energy || 3,
        minuteIndex: nearest,
      });
    } catch (err) {
      console.warn("[plan-basis] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        headline: "今天主攻言语，顺手回收昨天的错题",
        rationale: "距离目标分差 14 分，其中言语贡献了最大缺口；错题复盘是当天回收记忆成本最低的动作；申论轻量保温防生疏。",
        tasks: DEMO_TASKS,
      });
    }
  },

  onToggleAdjust() {
    this.setData({ adjustOpen: !this.data.adjustOpen });
  },

  onPickEnergy(e: WechatMiniprogram.TouchEvent) {
    const { value } = e.currentTarget.dataset as { value: number };
    this.setData({ energy: Number(value) });
  },

  onMinuteChange(e: WechatMiniprogram.SliderChange) {
    this.setData({ minuteIndex: Number(e.detail.value) });
  },

  async onApplyAdjust() {
    if (this.data.adjusting) return;
    const minutes = MINUTE_STEPS[this.data.minuteIndex] ?? 90;

    if (this.data.usingDemo) {
      wx.showToast({ title: `按 ${minutes} 分钟重排了（演示）`, icon: "none" });
      this.setData({ adjustOpen: false });
      return;
    }

    this.setData({ adjusting: true });
    try {
      await setTodayCapacity(
        {
          basedOnCapacityVersionId: this.data.capacityVersionId,
          availableMinutes: minutes,
          energy: this.data.energy,
          stress: 6 - this.data.energy, // 简化：精力低即压力高
          confidenceLevel: 3,
        },
        `capacity-${this.data.capacityVersionId}-${minutes}-${this.data.energy}`,
      );
      wx.showToast({ title: "重排好了，回今日页看新任务", icon: "none" });
      setTimeout(() => wx.switchTab({ url: "/pages/today/index" }), 1200);
    } catch (err) {
      console.error("[plan-basis] capacity failed", err);
      this.setData({ adjusting: false });
      wx.showToast({ title: "没调成功，再试一次", icon: "none" });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/today/index" }) });
  },
});
