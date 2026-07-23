import { authedV2, fetchOnboardingProgress, idemKey } from "../../../api/endpoints";
import { ApiError } from "../../../api/client";
import type {
  OnboardingProgress,
  StudyPreferenceWrite,
  WeeklyStudyPreference,
} from "../../../api/generated/types.gen";

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

const DAY_LABELS: Record<WeekDay, string> = {
  monday: "周一",
  tuesday: "周二",
  wednesday: "周三",
  thursday: "周四",
  friday: "周五",
  saturday: "周六",
  sunday: "周日",
};

const ALL_DAYS: WeekDay[] = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
];

function minuteOptions(values: number[]): PrefOption[] {
  return values.map((v) => ({ label: `${v} 分钟`, value: v }));
}

function dayOptions(days: WeekDay[]): PrefOption[] {
  return days.map((d) => ({ label: DAY_LABELS[d], value: d }));
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
    options: dayOptions(ALL_DAYS),
  },
  {
    key: "fixedRestDay",
    label: "固定休息日",
    options: dayOptions(ALL_DAYS),
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
    viewState: "loading" as "loading" | "error" | "ready",
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

  /** 当前偏好（写接口需要其中的 preferenceVersion 做乐观锁） */
  pref: null as WeeklyStudyPreference | null,
  /** 引导进度（写接口需要其中的 progressVersion） */
  progress: null as OnboardingProgress | null,

  onLoad() {
    const app = getApp<{ globalData: { statusBarHeight: number } }>();
    this.setData({ statusBarHeight: app.globalData.statusBarHeight });
    void this.load();
  },

  onBack() {
    wx.navigateBack({
      fail: () => {
        wx.switchTab({ url: "/pages/profile/index" });
      },
    });
  },

  onRetry() {
    void this.load();
  },

  async load() {
    this.setData({ viewState: "loading" });
    try {
      const [pref, progress] = await Promise.all([
        authedV2<WeeklyStudyPreference>({
          method: "GET",
          path: "/api/v2/onboarding/study-preference",
        }),
        fetchOnboardingProgress(),
      ]);
      this.pref = pref;
      this.progress = progress;
      const values: Record<PrefKey, number | string | null> = {
        weekdayMinutes: pref.weekdayMinutes,
        weekendMinutes: pref.weekendMinutes,
        fragmentMinutes: pref.fragmentMinutes,
        longBlockMinutes: pref.longBlockMinutes,
        mockExamDay: pref.mockExamDay,
        fixedRestDay: pref.fixedRestDay,
      };
      this.setData({ viewState: "ready", values });
      this.ensureMinuteOptions(values);
      this.refreshCanSubmit();
    } catch {
      this.setData({ viewState: "error" });
    }
  },

  /** 后端值不在预置分钟选项中时，追加为选项，避免回填丢值 */
  ensureMinuteOptions(values: Record<PrefKey, number | string | null>) {
    const minuteKeys: PrefKey[] = [
      "weekdayMinutes",
      "weekendMinutes",
      "fragmentMinutes",
      "longBlockMinutes",
    ];
    const groups = this.data.groups.map((group) => {
      if (!minuteKeys.includes(group.key)) return group;
      const value = values[group.key];
      if (typeof value !== "number" || group.options.some((o) => o.value === value)) {
        return group;
      }
      return {
        ...group,
        options: [...group.options, { label: `${value} 分钟`, value }].sort(
          (a, b) => (a.value as number) - (b.value as number),
        ),
      };
    });
    this.setData({ groups });
  },

  onPick(e: WechatMiniprogram.TouchEvent) {
    const key = e.currentTarget.dataset.key as PrefKey;
    const value = e.currentTarget.dataset.value as number | string;
    const values = { ...this.data.values, [key]: value };
    this.setData({ values });
    this.refreshCanSubmit();
  },

  refreshCanSubmit() {
    const canSubmit = Object.values(this.data.values).every((v) => v !== null);
    this.setData({ canSubmit });
  },

  buildBody(): StudyPreferenceWrite {
    const v = this.data.values;
    return {
      effectiveFrom: todayIso(),
      expectedPreferenceVersion: this.pref?.preferenceVersion ?? 0,
      expectedProgressVersion: this.progress?.progressVersion ?? 0,
      fixedRestDay: v.fixedRestDay as WeekDay,
      fragmentMinutes: v.fragmentMinutes as number,
      longBlockMinutes: v.longBlockMinutes as number,
      mockExamDay: v.mockExamDay as WeekDay,
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
      wx.showToast({ title: "已保存", icon: "success" });
    } catch {
      // 失败提示已在 writeWithRetry 内给出
    } finally {
      this.setData({ submitting: false });
    }
  },

  /** 写接口：409（版本冲突）时刷新版本号，用新版本号重试一次 */
  async writeWithRetry() {
    try {
      await authedV2<unknown>({
        method: "PUT",
        path: "/api/v2/onboarding/study-preference",
        body: this.buildBody(),
        idempotencyKey: idemKey(),
      });
      return;
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) {
        wx.showToast({ title: "网络不太稳定，稍后再试", icon: "none" });
        throw err;
      }
    }
    try {
      const [pref, progress] = await Promise.all([
        authedV2<WeeklyStudyPreference>({
          method: "GET",
          path: "/api/v2/onboarding/study-preference",
        }),
        fetchOnboardingProgress(),
      ]);
      this.pref = pref;
      this.progress = progress;
      await authedV2<unknown>({
        method: "PUT",
        path: "/api/v2/onboarding/study-preference",
        body: this.buildBody(),
        idempotencyKey: idemKey(),
      });
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
