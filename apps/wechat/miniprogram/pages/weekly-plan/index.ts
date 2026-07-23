import { fetchWeeklyPlan } from "../../api/endpoints";

type DayVM = {
  localDate: string;
  dayLabel: string;
  dateLabel: string;
  modeLabel: string;
  modeClass: string;
  minutes: number;
  progressLabel: string;
  explanation: string;
  status: string;
  isToday: boolean;
};

const WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"];
const MODE: Record<string, { label: string; cls: string }> = {
  training: { label: "训练", cls: "is-training" },
  mock: { label: "模考", cls: "is-mock" },
  recovery: { label: "休整", cls: "is-recovery" },
};

const DEMO_DAYS: DayVM[] = [
  { localDate: "2026-07-20", dayLabel: "一", dateLabel: "7/20", modeLabel: "训练", modeClass: "is-training", minutes: 90, progressLabel: "3/3", explanation: "言语专项 + 错题回收", status: "past", isToday: false },
  { localDate: "2026-07-21", dayLabel: "二", dateLabel: "7/21", modeLabel: "训练", modeClass: "is-training", minutes: 90, progressLabel: "2/3", explanation: "判断推理定向补弱", status: "past", isToday: false },
  { localDate: "2026-07-22", dayLabel: "三", dateLabel: "7/22", modeLabel: "训练", modeClass: "is-training", minutes: 60, progressLabel: "3/3", explanation: "资料分析速度训练", status: "past", isToday: false },
  { localDate: "2026-07-23", dayLabel: "四", dateLabel: "7/23", modeLabel: "训练", modeClass: "is-training", minutes: 90, progressLabel: "1/3", explanation: "申论素材 + 言语保温", status: "today", isToday: true },
  { localDate: "2026-07-24", dayLabel: "五", dateLabel: "7/24", modeLabel: "训练", modeClass: "is-training", minutes: 90, progressLabel: "0/3", explanation: "数量关系突破", status: "upcoming", isToday: false },
  { localDate: "2026-07-25", dayLabel: "六", dateLabel: "7/25", modeLabel: "模考", modeClass: "is-mock", minutes: 120, progressLabel: "0/1", explanation: "周模考：检验本周训练效果", status: "upcoming", isToday: false },
  { localDate: "2026-07-26", dayLabel: "日", dateLabel: "7/26", modeLabel: "休整", modeClass: "is-recovery", minutes: 0, progressLabel: "-", explanation: "固定休息日，大脑也要还账", status: "upcoming", isToday: false },
];

Page({
  data: {
    loading: true,
    usingDemo: false,
    weekLabel: "",
    rhythmLine: "",
    days: [] as DayVM[],
  },

  onLoad() {
    void this.load();
  },

  async load() {
    try {
      const plan = await fetchWeeklyPlan();
      this.setData({
        loading: false,
        weekLabel: `${plan.weekStart} 起的一周 · 第 ${plan.versionNo} 版`,
        rhythmLine: "节奏 5+1+1：五天训练，一天模考，一天休整",
        days: plan.days.map((d) => {
          const m = MODE[d.mode] ?? { label: d.mode, cls: "" };
          const parts = d.localDate.split("-");
          return {
            localDate: d.localDate,
            dayLabel: WEEKDAY[(d.weekday - 1 + 7) % 7] ?? "?",
            dateLabel: `${Number(parts[1])}/${Number(parts[2])}`,
            modeLabel: m.label,
            modeClass: m.cls,
            minutes: d.plannedMinutes,
            progressLabel: d.totalTaskCount > 0 ? `${d.completedTaskCount}/${d.totalTaskCount}` : "-",
            explanation: d.explanation,
            status: d.status,
            isToday: d.status === "today",
          };
        }),
      });
    } catch (err) {
      console.warn("[weekly-plan] fallback to demo", err);
      this.setData({
        loading: false,
        usingDemo: true,
        weekLabel: "本周（演示）",
        rhythmLine: "节奏 5+1+1：五天训练，一天模考，一天休整",
        days: DEMO_DAYS,
      });
    }
  },

  onTapDay(e: WechatMiniprogram.TouchEvent) {
    const { status } = e.currentTarget.dataset as { status: string };
    if (status === "today") {
      wx.switchTab({ url: "/pages/today/index" });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
