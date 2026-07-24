import { PlanningApi } from "../../api/planning";
import type {
  AppDomainsPlanningSchemasTodayTask,
  PlanBasisTask,
  PlanBasisView,
} from "../../api/generated/types.gen";
import { registeredTaskNavigationTarget, routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import {
  errorView,
  requireAuthenticatedClient,
  trackLabel,
} from "../../features/learning-brain/presentation";

type FactorTone = "primary" | "neutral" | "warning";

interface BasisFactorView {
  label: string;
  value: string;
  tone: FactorTone;
}

interface BasisTaskView extends PlanBasisTask {
  trackLabel: string;
  startUrl: string;
  objective: string;
  successCriteria: string[];
  requiresReflection: boolean;
}

function percent(value: number): string {
  return `${String(Math.round(value * 100))}%`;
}

function matchingTodayTask(
  task: PlanBasisTask,
  todayTasks: AppDomainsPlanningSchemasTodayTask[],
): AppDomainsPlanningSchemasTodayTask | null {
  return todayTasks.find((candidate) => candidate.id === task.taskId) ?? null;
}

function factorViews(task: PlanBasisTask): BasisFactorView[] {
  return [
    { label: "岗位收益", value: percent(task.priorityBreakdown.goalGain), tone: "primary" },
    { label: "能力收益", value: percent(task.priorityBreakdown.abilityGain), tone: "primary" },
    {
      label: "复测紧迫度",
      value: percent(task.priorityBreakdown.verificationUrgency),
      tone: "warning",
    },
    { label: "时间适配", value: percent(task.priorityBreakdown.timeFit), tone: "neutral" },
  ];
}

Page({
  data: {
    status: "loading",
    errorMessage: "",
    requestId: "",
    basis: null as PlanBasisView | null,
    focusTask: null as BasisTaskView | null,
    evidenceBullets: [] as string[],
    factorMetrics: [] as BasisFactorView[],
    invalidationConditions: [] as string[],
    expanded: false,
    openingTask: false,
  },

  onLoad() {
    void this.loadBasis();
  },

  async loadBasis() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ status: "loading", errorMessage: "", requestId: "" });
    try {
      const api = new PlanningApi(client);
      const [basisEnvelope, todayEnvelope] = await Promise.all([
        api.getTodayBasis(),
        api.getToday(),
      ]);
      const basis = basisEnvelope.data;
      const rawTask = basis.tasks[0] ?? null;
      const todayTask = rawTask ? matchingTodayTask(rawTask, todayEnvelope.data.tasks) : null;
      const focusTask: BasisTaskView | null =
        rawTask && todayTask
          ? {
              ...rawTask,
              trackLabel: trackLabel[rawTask.track],
              startUrl: todayTask.startUrl,
              objective: todayTask.completion.objective,
              successCriteria: todayTask.completion.successCriteria,
              requiresReflection: todayTask.completion.requiresReflection,
            }
          : null;
      const evidenceBullets = focusTask
        ? [
            focusTask.reason,
            `${String(focusTask.evidenceRefs.length)} 条与你有关的学习记录参与了本次排序`,
            basis.conflicts[0] ?? "当前判断仍需由本次训练与后续复测确认",
          ]
        : [];
      this.setData({
        status: focusTask ? "ready" : "empty",
        basis,
        focusTask,
        evidenceBullets,
        factorMetrics: rawTask ? factorViews(rawTask) : [],
        invalidationConditions: basis.invalidationConditions,
        errorMessage: focusTask ? "" : "今天暂时没有需要解释的训练任务。",
      });
    } catch (error) {
      const view = errorView(error, "今天的安排依据没有加载成功，请重试。");
      this.setData({
        status: "error",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  retry() {
    void this.loadBasis();
  },

  exit() {
    void wx.navigateBack({ delta: 1 });
  },

  toggleExpanded() {
    this.setData({ expanded: !this.data.expanded });
  },

  startTask() {
    const focusTask = this.data.focusTask;
    if (!focusTask || this.data.openingTask) return;
    const target = registeredTaskNavigationTarget(focusTask.startUrl);
    if (!target) {
      void wx.showModal({
        title: "这项任务暂时不能开始",
        content: "任务入口已经变化，请返回今日页面刷新后再试。",
        showCancel: false,
      });
      return;
    }
    this.setData({ openingTask: true });
    const complete = () => this.setData({ openingTask: false });
    if (target.mode === "switchTab") {
      wx.switchTab({ url: target.url, complete });
      return;
    }
    wx.navigateTo({ url: target.url, complete });
  },

  openAdjustment() {
    void wx.navigateTo({ url: routes.dayAdjustment });
  },

  askAnan() {
    const auth = currentAuthSession();
    const basis = this.data.basis;
    const focusTask = this.data.focusTask;
    if (!auth || !basis || !focusTask) return;
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "今天第一项任务",
      title: focusTask.title,
      summary: `${focusTask.reason}；预计 ${String(focusTask.estimatedMinutes)} 分钟。`,
      suggestedQuestion:
        "结合这项任务的排序依据、我的目标岗位和最近学习记录，解释为什么现在最值得做它。",
      routeId: "pages/plan-basis/index",
      workspace: "today",
      objectRefs: [
        {
          type: "plan",
          id: basis.planVersionId,
          version: String(basis.versionNo),
        },
      ],
    });
    void wx.switchTab({ url: routes.anan });
  },
});
