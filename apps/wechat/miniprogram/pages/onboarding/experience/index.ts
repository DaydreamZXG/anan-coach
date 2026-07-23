import { fetchOnboardingProgress, putPreparationExperience } from "../../../api/endpoints";
import { ApiError } from "../../../api/client";
import type {
  OnboardingProgress,
  PreparationExperienceWrite,
} from "../../../api/generated/types.gen";

type ExperienceKey = "firstSystematic" | "priorGuokaoExam" | "retake" | "externalLearning";

interface BoolOption {
  label: string;
  value: boolean;
}

interface ExperienceGroup {
  key: ExperienceKey;
  label: string;
  options: BoolOption[];
}

const GROUPS: ExperienceGroup[] = [
  {
    key: "firstSystematic",
    label: "这是你第一次系统备考吗？",
    options: [
      { label: "是", value: true },
      { label: "否", value: false },
    ],
  },
  {
    key: "priorGuokaoExam",
    label: "之前参加过国考吗？",
    options: [
      { label: "参加过", value: true },
      { label: "没参加过", value: false },
    ],
  },
  {
    key: "retake",
    label: "属于二战及以上吗？",
    options: [
      { label: "是", value: true },
      { label: "否", value: false },
    ],
  },
  {
    key: "externalLearning",
    label: "在用外部课程或资料吗？",
    options: [
      { label: "在用", value: true },
      { label: "没有", value: false },
    ],
  },
];

Page({
  data: {
    statusBarHeight: 20,
    steps: [1, 2, 3, 4, 5, 6],
    step: 1,
    groups: GROUPS,
    values: {
      firstSystematic: null,
      priorGuokaoExam: null,
      retake: null,
      externalLearning: null,
    } as Record<ExperienceKey, boolean | null>,
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
    const key = e.currentTarget.dataset.key as ExperienceKey;
    const value = e.currentTarget.dataset.value as boolean;
    const values = { ...this.data.values, [key]: value };
    this.setData({ values, canSubmit: Object.values(values).every((v) => v !== null) });
  },

  buildBody(progress: OnboardingProgress): PreparationExperienceWrite {
    const v = this.data.values;
    return {
      expectedExperienceVersion: progress.experienceProfileVersion ?? 0,
      expectedProgressVersion: progress.progressVersion,
      externalLearning: v.externalLearning ?? false,
      firstSystematic: v.firstSystematic ?? false,
      priorGuokaoExam: v.priorGuokaoExam ?? false,
      retake: v.retake ?? false,
    };
  },

  async onSubmit() {
    if (!this.data.canSubmit || this.data.submitting) return;
    this.setData({ submitting: true });
    try {
      await this.writeWithRetry();
      wx.redirectTo({ url: "/pages/onboarding/qualification/index" });
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
      await putPreparationExperience(this.buildBody(progress));
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
      await putPreparationExperience(this.buildBody(progress));
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
