import type {
  LearningBrainProfileVersion,
  ModuleAbility,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { PlanningApi } from "../../api/planning";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { scoreRangeLabel } from "../../domain/presentation";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import {
  dateTimeLabel,
  errorView,
  percent,
  readinessLabel,
  requireAuthenticatedClient,
} from "../../features/learning-brain/presentation";

type ScoreRange = { lower: number; upper: number };

interface RadarPoint {
  label: string;
  value: number;
}

type ModuleView = ModuleAbility & {
  metricLabel: string;
  readinessLabel: string;
  statusTone: "neutral" | "warning" | "primary";
  hasEvidence: boolean;
};

const XINGCE_MODULE_BASELINE: ReadonlyArray<Pick<ModuleAbility, "moduleKey" | "displayName">> = [
  { moduleKey: "政治理论", displayName: "政治理论" },
  { moduleKey: "常识判断", displayName: "常识判断" },
  { moduleKey: "言语理解与表达", displayName: "言语理解与表达" },
  { moduleKey: "数量关系", displayName: "数量关系" },
  { moduleKey: "判断推理", displayName: "判断推理" },
  { moduleKey: "资料分析", displayName: "资料分析" },
];

const ESSAY_MODULE_BASELINE: ReadonlyArray<Pick<ModuleAbility, "moduleKey" | "displayName">> = [
  { moduleKey: "point_finding", displayName: "找点与覆盖" },
  { moduleKey: "abstraction", displayName: "概括与归纳" },
  { moduleKey: "structure", displayName: "结构组织" },
  { moduleKey: "expression", displayName: "表达准确性" },
  { moduleKey: "format", displayName: "格式规范" },
  { moduleKey: "unsupported_claim", displayName: "材料依据" },
];

const EMPTY_XINGCE_MODULES = XINGCE_MODULE_BASELINE.map(emptyXingceModule);
const EMPTY_ESSAY_MODULES = ESSAY_MODULE_BASELINE.map(emptyEssayModule);

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    profile: null as LearningBrainProfileVersion | null,
    totalScoreLabel: "数据不足",
    xingceScoreLabel: "数据不足",
    essayScoreLabel: "数据不足",
    evidenceCountLabel: "0",
    externalEvidenceCountLabel: "0",
    executionCountLabel: "0",
    profileReadinessLabel: "正在核对",
    updatedLabel: "",
    subjectTab: "xingce",
    xingceModules: EMPTY_XINGCE_MODULES,
    essayModules: EMPTY_ESSAY_MODULES,
    radarPoints: EMPTY_XINGCE_MODULES.map(radarPoint),
    goalAvailable: false,
    goalName: "尚未确认主目标岗位",
    targetScoreLabel: "待建立",
    currentScoreLabel: "待建立",
    scoreGapLabel: "待建立",
    goalBoundaryLabel: "先确认主目标岗位并补齐同口径双科成绩。",
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "ability" });
    }
    void this.loadProfile();
  },

  async loadProfile() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    // 已渲染过内容时静默刷新，避免每次切回 Tab 都整页闪骨架屏。
    const silentRefresh = this.data.status === "ready";
    if (silentRefresh) {
      this.setData({ errorMessage: "", requestId: "" });
    } else {
      this.setData({ status: "loading", errorMessage: "", requestId: "" });
    }
    try {
      const [{ data: profile }, dashboardResult] = await Promise.all([
        new LearningBrainApi(client).getProfile(),
        new PlanningApi(client).getToday().catch(() => null),
      ]);
      const estimate = profile.ability.estimate;
      const trim = (value: number): string =>
        Number.isInteger(value) ? String(value) : value.toFixed(1);
      const score = (range: ScoreRange | null | undefined): string =>
        range ? `${trim(range.lower)}–${trim(range.upper)}` : "数据不足";
      const xingceModules = profile.modules
        .filter((module) => module.track === "xingce")
        .map(moduleView);
      const xingceModuleByKey = new Map(
        xingceModules.map((module) => [module.moduleKey, module] as const),
      );
      const visibleXingceModules = XINGCE_MODULE_BASELINE.map(
        (module) => xingceModuleByKey.get(module.moduleKey) ?? emptyXingceModule(module),
      );
      const essayModules = profile.modules
        .filter((module) => module.track === "essay")
        .map(moduleView);
      const essayModuleByKey = new Map(
        essayModules.map((module) => [module.moduleKey, module] as const),
      );
      const baselineEssayKeys = new Set(ESSAY_MODULE_BASELINE.map((module) => module.moduleKey));
      const visibleEssayModules = [
        ...ESSAY_MODULE_BASELINE.map(
          (module) => essayModuleByKey.get(module.moduleKey) ?? emptyEssayModule(module),
        ),
        ...essayModules.filter((module) => !baselineEssayKeys.has(module.moduleKey)),
      ];
      const evidenceCount =
        profile.evidenceSummary.scoreEvidenceCount +
        profile.evidenceSummary.xingceAttemptCount +
        profile.evidenceSummary.xingceMasteryCount +
        profile.evidenceSummary.essayMasteryCount +
        profile.evidenceSummary.taskExecutionCount +
        (profile.evidenceSummary.externalAnswerCount ?? 0);
      this.setData({
        status: "ready",
        profile,
        totalScoreLabel: score(estimate?.total),
        xingceScoreLabel: score(estimate?.xingce),
        essayScoreLabel: score(estimate?.shenlun),
        evidenceCountLabel: String(evidenceCount),
        externalEvidenceCountLabel: String(profile.evidenceSummary.externalAnswerCount ?? 0),
        executionCountLabel: String(profile.evidenceSummary.taskExecutionCount),
        profileReadinessLabel: readinessLabel(profile.readiness),
        updatedLabel: dateTimeLabel(profile.generatedAt),
        xingceModules: visibleXingceModules,
        essayModules: visibleEssayModules,
        radarPoints: visibleXingceModules.map(radarPoint),
        ...goalView(dashboardResult?.data ?? null),
      });
    } catch (error) {
      const view = errorView(error, "学习档案没有加载成功，请重试。");
      // 静默刷新失败时保留旧数据，只用行内提示说明刷新未成功。
      this.setData({
        status: silentRefresh ? "ready" : "error",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  chooseSubject(event: WechatMiniprogram.BaseEvent) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "xingce" || value === "essay") {
      this.setData({ subjectTab: value });
    }
  },

  openModule(event: WechatMiniprogram.BaseEvent) {
    const track = String(event.currentTarget.dataset.track ?? "");
    const moduleKey = String(event.currentTarget.dataset.key ?? "");
    if (!track || !moduleKey) return;
    void wx.navigateTo({
      url: `${routes.abilityModule}?track=${track}&moduleKey=${encodeURIComponent(moduleKey)}`,
    });
  },

  openPattern(event: WechatMiniprogram.BaseEvent) {
    const patternId = String(event.currentTarget.dataset.id ?? "");
    if (patternId) {
      void wx.navigateTo({
        url: `${routes.errorPattern}?patternId=${encodeURIComponent(patternId)}`,
      });
    }
  },

  openPlanBasis() {
    void wx.navigateTo({ url: routes.planBasis });
  },

  openCompanion() {
    const auth = currentAuthSession();
    if (!auth) {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    const profile = this.data.profile;
    if (!profile) return;
    const goalSummary = this.data.goalAvailable
      ? `主目标 ${this.data.goalName}，目标 ${this.data.targetScoreLabel}，当前 ${this.data.currentScoreLabel}，分差 ${this.data.scoreGapLabel}`
      : this.data.goalBoundaryLabel;
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "当前能力档案",
      title: `能力档案 · ${this.data.profileReadinessLabel}`,
      summary: [
        `综合估计 ${this.data.totalScoreLabel}`,
        `行测 ${this.data.xingceScoreLabel}`,
        `申论 ${this.data.essayScoreLabel}`,
        `${this.data.evidenceCountLabel} 条学习依据`,
        goalSummary,
      ].join("；"),
      suggestedQuestion:
        "结合我当前的能力、错因和目标岗位，告诉我最值得先改的一个问题，并说明依据和下一步动作。",
      routeId: "pages/ability/index",
      workspace: "profile",
      objectRefs: [],
    });
    void wx.switchTab({ url: routes.anan });
  },

  retry() {
    void this.loadProfile();
  },
});

function moduleView(module: ModuleAbility): ModuleView {
  const hasEvidence =
    module.evidenceCount > 0 ||
    (module.externalEvidenceCount ?? 0) > 0 ||
    (module.correctRate !== null && module.correctRate !== undefined);
  const speed =
    module.averageSeconds === null || module.averageSeconds === undefined
      ? ""
      : ` · 平均 ${String(Math.round(module.averageSeconds))} 秒`;
  return {
    ...module,
    hasEvidence,
    externalEvidenceCount: module.externalEvidenceCount ?? 0,
    metricLabel:
      module.track === "xingce"
        ? `${percent(module.correctRate)}${speed}`
        : `${String(module.evidenceCount)} 条有效作答与迁移记录`,
    readinessLabel: readinessLabel(module.readiness),
    statusTone:
      module.readiness === "reliable"
        ? "primary"
        : module.readiness === "stale" || module.readiness === "conflicted"
          ? "warning"
          : "neutral",
  };
}

function emptyXingceModule(module: Pick<ModuleAbility, "moduleKey" | "displayName">): ModuleView {
  return {
    ...module,
    track: "xingce",
    readiness: "unavailable",
    confidence: "unknown",
    freshness: "unavailable",
    evidenceCount: 0,
    externalEvidenceCount: 0,
    evidenceRefs: [],
    conflictCodes: [],
    dueCount: 0,
    stableMasteryCount: 0,
    correctRate: 0,
    averageSeconds: null,
    scoreBand: null,
    latestEvidenceAt: null,
    metricLabel: "0% · 0 次作答",
    readinessLabel: "待采样",
    statusTone: "neutral",
    hasEvidence: false,
  };
}

function emptyEssayModule(module: Pick<ModuleAbility, "moduleKey" | "displayName">): ModuleView {
  return {
    ...module,
    track: "essay",
    readiness: "unavailable",
    confidence: "unknown",
    freshness: "unavailable",
    evidenceCount: 0,
    externalEvidenceCount: 0,
    evidenceRefs: [],
    conflictCodes: [],
    dueCount: 0,
    stableMasteryCount: 0,
    correctRate: null,
    averageSeconds: null,
    scoreBand: null,
    latestEvidenceAt: null,
    metricLabel: "0 条有效作答与迁移记录",
    readinessLabel: "待采样",
    statusTone: "neutral",
    hasEvidence: false,
  };
}

function radarPoint(module: ModuleView): RadarPoint {
  return {
    label: module.displayName,
    value: Math.round((module.correctRate ?? 0) * 100),
  };
}

function goalView(dashboard: TodayDashboard | null) {
  const gap = dashboard?.goalGap;
  if (!gap) {
    return {
      goalAvailable: false,
      goalName: "岗位信息暂未载入",
      targetScoreLabel: "待建立",
      currentScoreLabel: "待建立",
      scoreGapLabel: "待建立",
      goalBoundaryLabel: "能力档案可用；岗位对照本次没有加载成功。",
    };
  }
  const targetScore = gap.targetScore;
  const currentScore = gap.currentScore;
  const baselineGap = gap.baselineGap;
  if (
    gap.readiness !== "complete" ||
    !targetScore ||
    !currentScore ||
    baselineGap === null ||
    baselineGap === undefined
  ) {
    return {
      goalAvailable: false,
      goalName: gap.positionName ?? "尚未确认主目标岗位",
      targetScoreLabel: gap.targetScore
        ? scoreRangeLabel(gap.targetScore.lower, gap.targetScore.upper)
        : "待建立",
      currentScoreLabel: gap.currentScore
        ? scoreRangeLabel(gap.currentScore.lower, gap.currentScore.upper)
        : "待建立",
      scoreGapLabel: "待建立",
      goalBoundaryLabel: gap.evidenceSummary || "还需主目标岗位和同一考试口径的行测、申论成绩。",
    };
  }
  return {
    goalAvailable: true,
    goalName: gap.positionName ?? "主目标岗位",
    targetScoreLabel: scoreRangeLabel(targetScore.lower, targetScore.upper),
    currentScoreLabel: scoreRangeLabel(currentScore.lower, currentScore.upper),
    scoreGapLabel: `${String(baselineGap)} 分`,
    goalBoundaryLabel: "用于备考定位，不代表录取结果。",
  };
}
