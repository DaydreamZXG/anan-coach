import {
  acknowledgeOnboarding,
  completeQualificationMilestone,
  fetchOnboardingProgress,
  putPreparationExperience,
  putStudyPreference,
  updateOnboardingProfile,
} from "../../api/endpoints";
import type { OnboardingProgress } from "../../api/generated/types.gen";

const EDUCATIONS = ["大专", "本科", "硕士", "博士"];
const POLITICAL = ["群众", "共青团员", "中共党员"];
const EXAM_YEARS = [2023, 2024, 2025, 2026];
/** 节奏预设：一次选择同时定下工作日/周末/长块/碎片时长 */
const RHYTHMS = [
  {
    key: "light",
    label: "在职·挤时间",
    desc: "工作日 45 分钟，周末 2 小时",
    weekdayMinutes: 45, weekendMinutes: 120, longBlockMinutes: 45, fragmentMinutes: 15,
  },
  {
    key: "steady",
    label: "稳定投入",
    desc: "工作日 90 分钟，周末 3 小时",
    weekdayMinutes: 90, weekendMinutes: 180, longBlockMinutes: 60, fragmentMinutes: 20,
  },
  {
    key: "full",
    label: "全职冲刺",
    desc: "每天 3 小时以上",
    weekdayMinutes: 200, weekendMinutes: 200, longBlockMinutes: 90, fragmentMinutes: 25,
  },
] as const;
const WEEKDAYS = [
  { value: "monday", label: "周一" },
  { value: "tuesday", label: "周二" },
  { value: "wednesday", label: "周三" },
  { value: "thursday", label: "周四" },
  { value: "friday", label: "周五" },
  { value: "saturday", label: "周六" },
  { value: "sunday", label: "周日" },
] as const;
const MODULES = ["言语理解", "判断推理", "资料分析", "数量关系", "常识判断"];

function today(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

Page({
  data: {
    step: 1,
    totalSteps: 4,
    educations: EDUCATIONS,
    political: POLITICAL,
    examYears: EXAM_YEARS,
    rhythms: RHYTHMS as unknown as Array<{ key: string; label: string; desc: string }>,
    weekdays: WEEKDAYS as unknown as Array<{ value: string; label: string }>,
    modules: MODULES,
    // 步骤一：备考经历
    priorExam: "" as "" | "yes" | "no",
    priorYears: [] as number[],
    firstSystematic: true,
    externalLearning: false,
    // 步骤二：资格身份
    education: "",
    major: "",
    politicalStatus: "",
    freshGraduate: true,
    // 步骤三：学习节奏
    studyMode: "" as "" | "fulltime" | "parttime",
    rhythmKey: "",
    restDay: "sunday",
    mockDay: "saturday",
    // 步骤四：基线自评
    weakModules: [] as string[],
    submitting: false,
  },

  onPick(e: WechatMiniprogram.TouchEvent) {
    const { field, value } = e.currentTarget.dataset as {
      field: string;
      value: string;
    };
    this.setData({ [field]: value });
  },

  onToggleYear(e: WechatMiniprogram.TouchEvent) {
    const { value } = e.currentTarget.dataset as { value: number };
    const year = Number(value);
    const list = this.data.priorYears.slice();
    const i = list.indexOf(year);
    if (i >= 0) list.splice(i, 1);
    else list.push(year);
    this.setData({ priorYears: list });
  },

  onFirstSystematic(e: WechatMiniprogram.SwitchChange) {
    this.setData({ firstSystematic: e.detail.value });
  },

  onExternalLearning(e: WechatMiniprogram.SwitchChange) {
    this.setData({ externalLearning: e.detail.value });
  },

  onMajor(e: WechatMiniprogram.Input) {
    this.setData({ major: e.detail.value });
  },

  onFresh(e: WechatMiniprogram.SwitchChange) {
    this.setData({ freshGraduate: e.detail.value });
  },

  onToggleModule(e: WechatMiniprogram.TouchEvent) {
    const { value } = e.currentTarget.dataset as { value: string };
    const list = this.data.weakModules.slice();
    const i = list.indexOf(value);
    if (i >= 0) list.splice(i, 1);
    else list.push(value);
    this.setData({ weakModules: list });
  },

  onNext() {
    const { step, priorExam, education, major, politicalStatus, studyMode, rhythmKey } = this.data;
    if (step === 1) {
      if (!priorExam) {
        wx.showToast({ title: "选一下是否考过", icon: "none" });
        return;
      }
      this.setData({ step: 2 });
      return;
    }
    if (step === 2) {
      if (!education || !major.trim() || !politicalStatus) {
        wx.showToast({ title: "把三项都填一下", icon: "none" });
        return;
      }
      this.setData({ step: 3 });
      return;
    }
    if (step === 3) {
      if (!studyMode || !rhythmKey) {
        wx.showToast({ title: "选一下模式和节奏", icon: "none" });
        return;
      }
      this.setData({ step: 4 });
      return;
    }
    this.submit();
  },

  onBack() {
    if (this.data.step > 1) this.setData({ step: this.data.step - 1 });
  },

  async submit() {
    if (this.data.submitting) return;
    this.setData({ submitting: true });
    const {
      priorExam, priorYears, firstSystematic, externalLearning,
      education, major, politicalStatus, freshGraduate,
      studyMode, rhythmKey, restDay, mockDay, weakModules,
    } = this.data;
    const rhythm = RHYTHMS.find((r) => r.key === rhythmKey) ?? RHYTHMS[1];

    try {
      // 按后端状态机逐步提交，每步前刷新进度获取乐观锁版本号
      let progress: OnboardingProgress = await fetchOnboardingProgress();

      // 步骤一：备考经历（含二战标记）
      const isRetake = priorExam === "yes";
      await putPreparationExperience({
        expectedExperienceVersion: progress.experienceProfileVersion ?? 0,
        expectedProgressVersion: progress.progressVersion,
        priorGuokaoExam: isRetake,
        retake: isRetake,
        priorExamYears: isRetake ? priorYears : [],
        firstSystematic,
        externalLearning,
      });

      // 步骤二：资格画像 + milestone 确认
      const profile = await updateOnboardingProfile({
        education,
        major: major.trim(),
        politicalStatus,
        freshGraduate,
        studyMode: studyMode || null,
        dailyMinutes: rhythm.weekdayMinutes,
        baselineSource: weakModules.length ? "self_report" : "none",
        baselineWeakModules: weakModules,
      });
      progress = await fetchOnboardingProgress();
      await completeQualificationMilestone({
        expectedProgressVersion: progress.progressVersion,
        qualificationProfileVersion:
          progress.qualificationProfileVersion ?? (profile as { profileVersion?: number }).profileVersion ?? 1,
      });

      // 步骤三：每周学习节奏
      progress = await fetchOnboardingProgress();
      await putStudyPreference({
        expectedPreferenceVersion: progress.studyPreferenceVersion ?? 0,
        expectedProgressVersion: progress.progressVersion,
        effectiveFrom: today(),
        weekdayMinutes: rhythm.weekdayMinutes,
        weekendMinutes: rhythm.weekendMinutes,
        longBlockMinutes: rhythm.longBlockMinutes,
        fragmentMinutes: rhythm.fragmentMinutes,
        fixedRestDay: restDay as "sunday",
        mockExamDay: mockDay as "saturday",
        timezone: "Asia/Shanghai",
      });

      // 确认结果，完成引导
      progress = await fetchOnboardingProgress();
      const result = await acknowledgeOnboarding(progress.progressVersion);
      if (result.nextAction === "start_retake_recovery") {
        wx.showToast({ title: "岸岸已按二战节奏排计划", icon: "none", duration: 2000 });
      }
    } catch (err) {
      console.error("[onboarding] submit failed (continuing to workspace)", err);
    }
    wx.switchTab({ url: "/pages/today/index" });
  },
});
