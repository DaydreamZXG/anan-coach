import { AccountApi } from "../../../api/account";
import { ApiClient, ApiClientError } from "../../../api/client";
import type {
  AbilityBaselineProfile,
  OnboardingProgress,
  PositionPreferenceProfile,
  PreparationExperienceProfile,
  QualificationFact,
  QualificationProfile,
  WeeklyStudyPreference,
} from "../../../api/generated/types.gen";
import { LearningBrainApi } from "../../../api/learning-brain";
import { OnboardingApi } from "../../../api/onboarding";
import { PlanningApi } from "../../../api/planning";
import { QualificationApi } from "../../../api/qualification";
import {
  onboardingNavigationTarget,
  routes,
  type RegisteredNavigationTarget,
} from "../../../app/navigation/routes";
import { currentAccessToken } from "../../../auth/session";

type ArchiveModuleKey = "identity" | "qualification" | "preference" | "time" | "ability";

interface ArchiveModuleView {
  key: ArchiveModuleKey;
  name: string;
  description: string;
  icon: string;
  tone: string;
  statusLabel: string;
  statusKind: "complete" | "incomplete";
}

Page({
  data: {
    status: "loading" as "loading" | "ready" | "error",
    errorMessage: "",
    requestId: "",
    warningTitle: "",
    warningCopy: "",
    modules: [] as ArchiveModuleView[],
    assessmentTarget: null as RegisteredNavigationTarget | null,
  },

  onShow() {
    void this.loadArchive();
  },

  back() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.switchTab({ url: routes.me });
  },

  retry() {
    void this.loadArchive();
  },

  async loadArchive() {
    const token = currentAccessToken();
    if (!token) {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    const client = new ApiClient(undefined, token);
    const onboarding = new OnboardingApi(client);
    try {
      const [accountResponse, progressResponse, qualificationResponse, planningResponse, brainResponse] =
        await Promise.all([
          new AccountApi(client).getOverview(),
          onboarding.getProgress(),
          new QualificationApi(client).getProfile(),
          new PlanningApi(client).getToday(),
          new LearningBrainApi(client).getProfile(),
        ]);
      const progress = progressResponse.data;
      const [experience, positionPreference, preference, abilityBaseline] = await Promise.all([
        progress.experienceProfileVersion
          ? onboarding.getPreparationExperience().then((response) => response.data)
          : Promise.resolve(null),
        optionalOnboardingRecord(
          onboarding.getPositionPreference().then((response) => response.data),
          "position_preference_missing",
        ),
        progress.studyPreferenceReady
          ? onboarding.getStudyPreference().then((response) => response.data)
          : Promise.resolve(null),
        optionalOnboardingRecord(
          onboarding.getAbilityBaseline().then((response) => response.data),
          "ability_baseline_missing",
        ),
      ]);
      const modules = buildModules({
        displayName: accountResponse.data.displayName,
        progress,
        qualification: qualificationResponse.data,
        experience,
        positionPreference,
        preference,
        abilityBaseline,
        positionName: planningResponse.data.goalGap.positionName,
        abilityReadiness: brainResponse.data.readiness,
      });
      const incompleteCount = modules.filter((item) => item.statusKind === "incomplete").length;
      this.setData({
        status: "ready",
        modules,
        warningTitle:
          incompleteCount > 0 ? "档案完整度影响岗位推荐" : "备考档案已完成关键校验",
        warningCopy:
          incompleteCount > 0
            ? `你目前有 ${String(incompleteCount)} 项关键信息待完善，这会限制岗位筛选、能力判断或计划容量。`
            : "身份、资格、岗位目标、学习时间和能力起点均有可追溯依据。",
        assessmentTarget: onboardingNavigationTarget(
          progress.resultReady ? "result" : progress.nextStep,
        ),
      });
    } catch (error) {
      if (error instanceof ApiClientError && error.statusCode === 401) {
        void wx.reLaunch({ url: routes.login });
        return;
      }
      const apiError = error instanceof ApiClientError ? error : undefined;
      this.setData({
        status: "error",
        errorMessage: apiError?.message ?? "备考档案没有加载成功，请重试。",
        requestId: apiError?.requestId ?? "",
      });
    }
  },

  editModule(event: WechatMiniprogram.BaseEvent) {
    const key = String(event.currentTarget.dataset.key ?? "") as ArchiveModuleKey;
    switch (key) {
      case "identity":
        void wx.switchTab({ url: routes.me });
        return;
      case "qualification":
        void wx.navigateTo({ url: routes.qualificationProfile });
        return;
      case "preference":
        void wx.navigateTo({ url: routes.onboardingPositionPreference });
        return;
      case "time":
        void wx.navigateTo({ url: routes.studyPreference });
        return;
      case "ability":
        void wx.switchTab({ url: routes.ability });
        return;
    }
  },

  openAssessment() {
    const target = this.data.assessmentTarget;
    if (!target) return;
    if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
    else void wx.navigateTo({ url: target.url });
  },
});

function buildModules(input: {
  displayName?: string | null | undefined;
  progress: OnboardingProgress;
  qualification: QualificationProfile;
  experience: PreparationExperienceProfile | null;
  positionPreference: PositionPreferenceProfile | null;
  preference: WeeklyStudyPreference | null;
  abilityBaseline: AbilityBaselineProfile | null;
  positionName?: string | null | undefined;
  abilityReadiness: "unavailable" | "partial" | "ready" | "stale" | "conflicted";
}): ArchiveModuleView[] {
  const experienceLabel = input.experience
    ? input.experience.retake
      ? "二战 / 多战"
      : input.experience.firstSystematic
        ? "首次备考"
        : "已有备考经历"
    : "尚未确认备考经历";
  const accountLabel = input.displayName?.trim();
  const statusLabel = {
    parttime: "在职",
    fulltime: "全职",
    student: "在校",
    unspecified: "",
  }[input.experience?.currentStatus ?? "unspecified"];
  const qualificationDescription = qualificationSummary(input.qualification);
  const abilityComplete = input.abilityReadiness === "ready";
  return [
    {
      key: "identity",
      name: "身份与状态",
      description: [experienceLabel, statusLabel, accountLabel].filter(Boolean).join(" · "),
      icon: "person",
      tone: "blue",
      statusLabel: input.progress.experienceProfileVersion ? "100%" : "待完善",
      statusKind: input.progress.experienceProfileVersion ? "complete" : "incomplete",
    },
    {
      key: "qualification",
      name: "报考资格",
      description: qualificationDescription,
      icon: "shield",
      tone: "orange",
      statusLabel: input.progress.qualificationReady ? "100%" : "待完善",
      statusKind: input.progress.qualificationReady ? "complete" : "incomplete",
    },
    {
      key: "preference",
      name: "岗位偏好",
      description: positionPreferenceSummary(
        input.positionPreference,
        input.positionName,
      ),
      icon: "pin",
      tone: "green",
      statusLabel: input.positionPreference ? "已确认" : "待完善",
      statusKind: input.positionPreference ? "complete" : "incomplete",
    },
    {
      key: "time",
      name: "学习时间",
      description: input.preference
        ? `工作日 ${hours(input.preference.weekdayMinutes)} · 周末 ${hours(input.preference.weekendMinutes)}`
        : "尚未设置每周可学时间",
      icon: "clock",
      tone: "indigo",
      statusLabel: input.preference ? "100%" : "待完善",
      statusKind: input.preference ? "complete" : "incomplete",
    },
    {
      key: "ability",
      name: "能力基础",
      description: abilityDescription(input.abilityBaseline, abilityComplete),
      icon: "target",
      tone: "rose",
      statusLabel: input.abilityBaseline
        ? input.abilityBaseline.mode === "unknown"
          ? "已安排"
          : "已录入"
        : "待完善",
      statusKind: input.abilityBaseline ? "complete" : "incomplete",
    },
  ];
}

function positionPreferenceSummary(
  preference: PositionPreferenceProfile | null,
  positionName?: string | null,
): string {
  if (!preference) return "尚未确认地区与岗位层级";
  const region = preference.preferredRegions.join("、");
  const relocation = preference.acceptsRelocation ? "接受调剂" : "不接受调剂";
  const target = positionName?.trim();
  return [region, relocation, target].filter(Boolean).join(" · ");
}

function abilityDescription(
  baseline: AbilityBaselineProfile | null,
  abilityComplete: boolean,
): string {
  if (!baseline) return "尚未确认能力起点";
  if (abilityComplete) return "双科能力证据已形成当前起点";
  if (baseline.mode === "unknown") return "已安排基础摸底，尚未形成双科区间";
  const scores = [
    baseline.xingceScore === null || baseline.xingceScore === undefined
      ? ""
      : `行测 ${String(baseline.xingceScore)}`,
    baseline.shenlunScore === null || baseline.shenlunScore === undefined
      ? ""
      : `申论 ${String(baseline.shenlunScore)}`,
  ].filter(Boolean);
  return `${scores.join(" · ")} · 自报待校准`;
}

async function optionalOnboardingRecord<T>(
  request: Promise<T>,
  missingCode: string,
): Promise<T | null> {
  try {
    return await request;
  } catch (error) {
    if (
      error instanceof ApiClientError &&
      error.statusCode === 404 &&
      error.payload?.code === missingCode
    ) {
      return null;
    }
    throw error;
  }
}

function qualificationSummary(profile: QualificationProfile): string {
  const education = factText(profile, "education");
  const major = factText(profile, "major");
  const grassroots = factText(profile, "service_project");
  return [education, major, grassroots && grassroots !== "无" ? grassroots : ""]
    .filter(Boolean)
    .join(" · ") || "学历、专业、基层经历等";
}

function factText(profile: QualificationProfile, key: QualificationFact["key"]): string {
  const value = profile.facts.find((item) => item.key === key)?.value;
  return typeof value === "string" ? value.trim() : "";
}

function hours(minutes: number): string {
  const value = minutes / 60;
  return `${Number.isInteger(value) ? String(value) : value.toFixed(1)}h`;
}
