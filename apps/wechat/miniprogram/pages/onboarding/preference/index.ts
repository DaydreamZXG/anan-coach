import { fetchOnboardingProgress, putStudyPreference } from "../../../api/endpoints";
import { ApiError } from "../../../api/client";
import type { OnboardingProgress, StudyPreferenceWrite } from "../../../api/generated/types.gen";

type WeekDay = "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday";

type PrefKey =
  | "weekdayMinutes"
  | "weekendMinutes"
  | "fragmentMinutes"
  | "longBlockMinutes"
  | "mockExamDay"
  | "fixedRestDay";

interface PrefOption {
  label: string;
  value: number | string;
}

interface PrefGroup {
  key: PrefKey;
  label: string;
  options: PrefOption[];
}

function minuteOptions(values: number[]): PrefOption[] {
  return values.map((v) => ({ label: `${v} 分钟`, value: v }));
}

const GROUPS: PrefGroup[] = [
  {
    key: "weekdayMinutes",
    label: "工作日每天可投入",
    options: minuteOptions([30, 60, 90, 120, 180]),
  },
  {
    key: "weekendMinutes",
    label: "周末每天可投入",
    options: minuteOptions([60, 120, 180, 240, 300]),
  },
  {
    key: "fragmentMinutes",
    label: "碎片时间单次",
    options: minuteOptions([10, 15, 20, 30]),
  },
  {
    key: "longBlockMinutes",
    label: "整块时间单次",
    options: minuteOptions([45, 60, 90, 120]),
  },
  {
    key: "mockExamDay",
    label: "每周模考日",
    options: [
      { label: "周六", value: "saturday" },
      { label: "周日", value: "sunday" },
    ],
  },
  {
    key: "fixedRestDay",
    label: "固定休息日",
    options: [
      { label: "周一", value: "monday" },
      { label: "周二", value: "tuesday" },
      { label: "周三", value: "wednesday" },
      { label: "周四", value: "thursday" },
      { label: "周五", value: "friday" },
      { label: "周六", value: "saturday" },
      { label: "周日", value: "sunday" },
      { label: "不固定", value: "flexible" },
    ],
  },
];

/** 今天的 ISO 日期（YYYY-MM-DD，本地时区） */
function todayIso(): string {
  const d = new Date();
  const m = `${d.getMonth() + 1}`.padStart(2, "0");
  const day = `${d.getDate()}`.padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

Page({
  data: {
    statusBarHeight: 20,
    steps: [1, 2, 3, 4, 5, 6],
    step: 4,
    groups: GROUPS,
    values: {
      weekdayMinutes: null,
      weekendMinutes: null,
      fragmentMinutes: null,
      longBlockMinutes: null,
      mockExamDay: null,
      fixedRestDay: null,
    } as Record<PrefKey, number | string | null>,
    canSubmit: false,
    submitting: false,
  },

  /** 引导进度（写接口需要其中的版本号） */
  progress: null as OnboardingProgress | null,

  async onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    try {
      this.progress = await fetchOnboardingProgress();
    } catch {
      wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
    }
  },

  onPick(e: WechatMiniprogram.TouchEvent) {
    const key = e.currentTarget.dataset.key as PrefKey;
    const value = e.currentTarget.dataset.value as number | string;
    const values = { ...this.data.values, [key]: value };
    this.setData({ values, canSubmit: Object.values(values).every((v) => v !== null) });
  },

  buildBody(progress: OnboardingProgress): StudyPreferenceWrite {
    const v = this.data.values;
    return {
      effectiveFrom: todayIso(),
      expectedPreferenceVersion: progress.studyPreferenceVersion ?? 0,
      expectedProgressVersion: progress.progressVersion,
      // 不固定休息时默认按周五处理
      fixedRestDay: (v.fixedRestDay === "flexible" ? "friday" : v.fixedRestDay) as WeekDay,
      fragmentMinutes: v.fragmentMinutes as number,
      longBlockMinutes: v.longBlockMinutes as number,
      mockExamDay: v.mockExamDay as "saturday" | "sunday",
      timezone: "Asia/Shanghai",
      weekdayMinutes: v.weekdayMinutes as number,
      weekendMinutes: v.weekendMinutes as number,
    };
  },

  async onSubmit() {
    if (!this.data.canSubmit || this.data.submitting) return;
    this.setData({ submitting: true });
    try {
      await this.writeWithRetry();
      wx.redirectTo({ url: "/pages/onboarding/placement/index" });
    } catch {
      // 失败提示已在 writeWithRetry 内给出
    } finally {
      this.setData({ submitting: false });
    }
  },

  /** 写接口：409（版本冲突）时刷新进度，用新版本号重试一次 */
  async writeWithRetry() {
    try {
      const progress = this.progress ?? (await fetchOnboardingProgress());
      await putStudyPreference(this.buildBody(progress));
      return;
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) {
        wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
        throw err;
      }
    }
    try {
      const progress = await fetchOnboardingProgress();
      this.progress = progress;
      await putStudyPreference(this.buildBody(progress));
    } catch (retryErr) {
      const title =
        retryErr instanceof ApiError && retryErr.status === 409
          ? "状态已变化，请重试"
          : "网络不太稳定，稍后再试";
      wx.showToast({ title, icon: "none" });
      throw retryErr;
    }
  },
});
