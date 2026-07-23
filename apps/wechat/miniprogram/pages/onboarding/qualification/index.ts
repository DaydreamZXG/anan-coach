import {
  completeQualificationMilestone,
  fetchOnboardingProgress,
  updateOnboardingProfile,
} from "../../../api/endpoints";
import { ApiError } from "../../../api/client";
import type { OnboardingProgress, OnboardingProfileUpdate } from "../../../api/generated/types.gen";

interface YearsOption {
  label: string;
  value: number;
}

/** 存入本地、供岗位搜索页使用的资格信息 */
interface QualificationInfo {
  education: string;
  major: string;
  politicalStatus: string;
  freshGraduate: boolean;
  grassrootsYears: number;
}

const EDUCATION_OPTIONS = ["大专", "本科", "硕士", "博士"];
const POLITICAL_OPTIONS = ["群众", "共青团员", "中共党员（含预备）", "民主党派", "其他"];
const YEARS_OPTIONS: YearsOption[] = [
  { label: "无", value: 0 },
  { label: "1 年", value: 1 },
  { label: "2 年", value: 2 },
  { label: "3 年及以上", value: 3 },
];

Page({
  data: {
    statusBarHeight: 20,
    steps: [1, 2, 3, 4, 5, 6],
    step: 2,
    educationOptions: EDUCATION_OPTIONS,
    politicalOptions: POLITICAL_OPTIONS,
    yearsOptions: YEARS_OPTIONS,
    education: "",
    politicalStatus: "",
    freshGraduate: null as boolean | null,
    grassrootsYears: null as number | null,
    major: "",
    canSubmit: false,
    submitting: false,
  },

  /** 引导进度（milestone 写接口需要其中的版本号） */
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

  onPickEducation(e: WechatMiniprogram.TouchEvent) {
    this.setData({ education: e.currentTarget.dataset.value as string });
    this.refreshCanSubmit();
  },

  onPickPolitical(e: WechatMiniprogram.TouchEvent) {
    this.setData({ politicalStatus: e.currentTarget.dataset.value as string });
    this.refreshCanSubmit();
  },

  onPickFresh(e: WechatMiniprogram.TouchEvent) {
    this.setData({ freshGraduate: e.currentTarget.dataset.value as boolean });
    this.refreshCanSubmit();
  },

  onPickYears(e: WechatMiniprogram.TouchEvent) {
    this.setData({ grassrootsYears: e.currentTarget.dataset.value as number });
    this.refreshCanSubmit();
  },

  onMajorInput(e: WechatMiniprogram.Input) {
    this.setData({ major: e.detail.value });
    this.refreshCanSubmit();
  },

  refreshCanSubmit() {
    const d = this.data;
    const canSubmit =
      d.education !== "" &&
      d.politicalStatus !== "" &&
      d.freshGraduate !== null &&
      d.grassrootsYears !== null &&
      d.major.trim() !== "";
    this.setData({ canSubmit });
  },

  async onSubmit() {
    if (!this.data.canSubmit || this.data.submitting) return;
    const d = this.data;
    const body: OnboardingProfileUpdate = {
      education: d.education,
      major: d.major.trim(),
      politicalStatus: d.politicalStatus,
      freshGraduate: d.freshGraduate ?? false,
      grassrootsYears: d.grassrootsYears ?? 0,
    };
    this.setData({ submitting: true });
    try {
      // 第一步：写资格画像
      await updateOnboardingProfile(body);
      // 第二步：确认资格 milestone（版本号需为画像写入后的最新值）
      await this.completeMilestoneWithRetry();
      // 岗位搜索页要用这份资格信息
      const info: QualificationInfo = {
        education: body.education,
        major: body.major,
        politicalStatus: body.politicalStatus,
        freshGraduate: body.freshGraduate,
        grassrootsYears: body.grassrootsYears ?? 0,
      };
      wx.setStorageSync("anan.qualification", info);
      wx.redirectTo({ url: "/pages/onboarding/position/index" });
    } catch {
      // 失败提示已在内部给出
    } finally {
      this.setData({ submitting: false });
    }
  },

  /** milestone：409（版本冲突）时刷新进度，用新版本号重试一次 */
  async completeMilestoneWithRetry() {
    try {
      const progress = await fetchOnboardingProgress();
      this.progress = progress;
      await completeQualificationMilestone({
        expectedProgressVersion: progress.progressVersion,
        qualificationProfileVersion: progress.qualificationProfileVersion ?? 0,
      });
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
      await completeQualificationMilestone({
        expectedProgressVersion: progress.progressVersion,
        qualificationProfileVersion: progress.qualificationProfileVersion ?? 0,
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
