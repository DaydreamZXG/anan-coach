import { fetchOnboardingProgress, putStudyPreference } from "../../api/endpoints";

const RHYTHMS = [
  {
    key: "light",
    label: "在职·挤时间",
    desc: "工作日 45 分钟，周末 120 分钟",
    weekdayMinutes: 45,
    weekendMinutes: 120,
    longBlockMinutes: 45,
    fragmentMinutes: 15,
  },
  {
    key: "steady",
    label: "稳定投入",
    desc: "每天 90 分钟，节奏均匀",
    weekdayMinutes: 90,
    weekendMinutes: 120,
    longBlockMinutes: 60,
    fragmentMinutes: 20,
  },
  {
    key: "sprint",
    label: "全职冲刺",
    desc: "每天 240 分钟以上，大块推进",
    weekdayMinutes: 240,
    weekendMinutes: 240,
    longBlockMinutes: 120,
    fragmentMinutes: 30,
  },
] as const;

const REST_DAYS = [
  { key: "monday", label: "周一" },
  { key: "tuesday", label: "周二" },
  { key: "wednesday", label: "周三" },
  { key: "thursday", label: "周四" },
  { key: "friday", label: "周五" },
  { key: "saturday", label: "周六" },
  { key: "sunday", label: "周日" },
] as const;

function today(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

Page({
  data: {
    rhythms: RHYTHMS.map((r) => ({ key: r.key, label: r.label, desc: r.desc })),
    restDays: REST_DAYS.map((r) => ({ key: r.key, label: r.label })),
    rhythmKey: "steady",
    restDayKey: "sunday",
    saving: false,
  },

  onPickRhythm(e: WechatMiniprogram.TouchEvent) {
    const { key } = e.currentTarget.dataset as { key: string };
    this.setData({ rhythmKey: key });
  },

  onPickRest(e: WechatMiniprogram.TouchEvent) {
    const { key } = e.currentTarget.dataset as { key: string };
    this.setData({ restDayKey: key });
  },

  async onSave() {
    if (this.data.saving) return;
    const rhythm = RHYTHMS.find((r) => r.key === this.data.rhythmKey);
    if (!rhythm) return;
    this.setData({ saving: true });

    try {
      const progress = await fetchOnboardingProgress();
      const restDay = this.data.restDayKey as
        | "monday" | "tuesday" | "wednesday" | "thursday"
        | "friday" | "saturday" | "sunday";
      // 模考日固定周六；若休息日撞周六则挪到周日
      const mockExamDay = restDay === "saturday" ? "sunday" : "saturday";
      await putStudyPreference({
        expectedPreferenceVersion: progress.studyPreferenceVersion ?? 0,
        expectedProgressVersion: progress.progressVersion,
        effectiveFrom: today(),
        weekdayMinutes: rhythm.weekdayMinutes,
        weekendMinutes: rhythm.weekendMinutes,
        longBlockMinutes: rhythm.longBlockMinutes,
        fragmentMinutes: rhythm.fragmentMinutes,
        fixedRestDay: restDay,
        mockExamDay,
      });
      wx.showToast({ title: "改好了，明天的计划就按新节奏排", icon: "none" });
      setTimeout(() => wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) }), 1200);
    } catch (err) {
      console.warn("[study-preference] save failed (demo mode)", err);
      wx.showToast({ title: "已记下（后端未连接，暂存本地）", icon: "none" });
      wx.setStorageSync("demo_study_preference", {
        rhythmKey: this.data.rhythmKey,
        restDayKey: this.data.restDayKey,
      });
      setTimeout(() => wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) }), 1200);
    } finally {
      this.setData({ saving: false });
    }
  },

  onLoad() {
    const saved = wx.getStorageSync("demo_study_preference") as
      | { rhythmKey?: string; restDayKey?: string }
      | "";
    if (saved && typeof saved === "object") {
      this.setData({
        rhythmKey: saved.rhythmKey ?? "steady",
        restDayKey: saved.restDayKey ?? "sunday",
      });
    }
  },

  onBack() {
    wx.navigateBack({ fail: () => wx.switchTab({ url: "/pages/me/index" }) });
  },
});
