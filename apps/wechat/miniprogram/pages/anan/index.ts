import { ApiClientError } from "../../api/client";
import { CompanionApi } from "../../api/companion";
import type {
  CompanionAskResponse,
  LearningBrainProfileVersion,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { PlanningApi } from "../../api/planning";
import { routes } from "../../app/navigation/routes";
import {
  companionAnswerView,
  type CompanionAnswerView,
} from "../../features/companion/presentation";
import { resolveCompanionAction } from "../../features/learning-brain/companion-action";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { createIdempotencyKey } from "../../platform/command-id";
import {
  consumeCompanionLaunchContext,
  type CompanionLaunchContext,
} from "../../features/companion/context-store";
import { currentAuthSession } from "../../auth/session";

const stateLabels: Record<CompanionAskResponse["semanticState"], string> = {
  ready: "已根据当前证据回答",
  evidence_insufficient: "依据不足",
  permission_denied: "当前页面无权回答",
  security_blocked: "已阻断不安全请求",
  quota_exhausted: "今日额度已用完",
  provider_pending: "回答结果待安全确认",
  failed: "本次回答未完成",
};

type PrimaryInsightAction = "mastery" | "positions" | "today" | "adjustment";

function defaultCompanionWorkspace(): CompanionLaunchContext["workspace"] {
  return "training";
}

Page({
  data: {
    contextStatus: "loading",
    learnerStateVersion: "",
    question: "",
    submitting: false,
    errorMessage: "",
    requestId: "",
    semanticState: "ready",
    stateLabel: "正在读取学习记录",
    companionIntent: "companion",
    answer: null as CompanionAnswerView | null,
    contextGoalLabel: "正在读取",
    contextTaskLabel: "正在读取",
    contextReviewLabel: "正在读取",
    contextCapacityLabel: "正在读取",
    contextEvidenceLabel: "0 条学习记录",
    insightTitle: "正在同步你的备考状态",
    insightCopy: "岗位、能力和今天的计划会在这里汇合。",
    insightActionLabel: "查看今天",
    insightAction: "today",
    insightDismissed: false,
    contextBannerVisible: false,
    contextSourceLabel: "",
    contextTitle: "",
    contextSummary: "",
    askRouteId: "pages/anan/index",
    askWorkspace: defaultCompanionWorkspace(),
    askObjectRefs: [] as CompanionLaunchContext["objectRefs"],
    askTaskMode: undefined as CompanionLaunchContext["taskMode"],
    askTaskStage: undefined as CompanionLaunchContext["taskStage"],
  },

  onLoad(options: Record<string, string | undefined>) {
    const question = options.question?.trim() ?? "";
    this.setData({ question: question.slice(0, 800) });
    void this.loadContext();
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "anan" });
    }
    this.applyLaunchContext();
  },

  applyLaunchContext() {
    const auth = currentAuthSession();
    if (!auth) return;
    const context = consumeCompanionLaunchContext(auth.userId);
    if (!context) return;
    this.setData({
      contextBannerVisible: true,
      contextSourceLabel: context.sourceLabel,
      contextTitle: context.title,
      contextSummary: context.summary,
      question: context.suggestedQuestion,
      answer: null,
      errorMessage: "",
      askRouteId: context.routeId,
      askWorkspace: context.workspace,
      askObjectRefs: context.objectRefs,
      askTaskMode: context.taskMode,
      askTaskStage: context.taskStage,
    });
  },

  clearLaunchContext() {
    this.setData({
      contextBannerVisible: false,
      contextSourceLabel: "",
      contextTitle: "",
      contextSummary: "",
      askRouteId: "pages/anan/index",
      askWorkspace: "training",
      askObjectRefs: [],
      askTaskMode: undefined,
      askTaskStage: undefined,
    });
  },

  async loadContext() {
    const client = requireAuthenticatedClient();
    if (!client) return;
    this.setData({ contextStatus: "loading", errorMessage: "", requestId: "" });
    try {
      const [{ data: profile }, dashboardResult] = await Promise.all([
        new LearningBrainApi(client).getProfile(),
        new PlanningApi(client).getToday().catch(() => null),
      ]);
      this.setData({
        contextStatus: "ready",
        learnerStateVersion: profile.profileVersionId,
        stateLabel: "可以问当前学习、计划或岗位问题",
        insightDismissed: false,
        ...companionContextView(profile, dashboardResult?.data ?? null),
      });
    } catch (error) {
      const view = errorView(error, "学习记录没有加载成功，暂时不能提交问题。");
      this.setData({
        contextStatus: "error",
        semanticState: "failed",
        stateLabel: "学习记录同步失败",
        errorMessage: view.message,
        requestId: view.requestId,
        companionIntent: "reminder",
      });
    }
  },

  inputQuestion(event: WechatMiniprogram.Input) {
    this.setData({ question: event.detail.value });
  },

  choosePrompt(event: WechatMiniprogram.BaseEvent) {
    const question = String(event.currentTarget.dataset.question ?? "").trim();
    if (question) this.setData({ question });
  },

  async ask() {
    const client = requireAuthenticatedClient();
    if (!client || this.data.submitting || this.data.contextStatus !== "ready") return;
    const question = this.data.question.trim();
    if (question.length < 4) {
      this.setData({ errorMessage: "请至少写 4 个字，让岸岸知道你在问什么。" });
      return;
    }
    this.setData({
      submitting: true,
      errorMessage: "",
      requestId: "",
      answer: null,
      companionIntent: "thinking",
      stateLabel: "正在检查权限并检索当前依据",
    });
    try {
      const { data } = await new CompanionApi(client).ask(
        {
          questionText: question,
          context: {
            routeId: this.data.askRouteId,
            workspace: this.data.askWorkspace,
            objectRefs: this.data.askObjectRefs,
            ...(this.data.askTaskMode && this.data.askTaskStage
              ? {
                  taskMode: this.data.askTaskMode,
                  taskStage: this.data.askTaskStage,
                }
              : {}),
            learnerStateVersion: this.data.learnerStateVersion,
          },
        },
        await createIdempotencyKey("companion"),
      );
      this.setData({
        submitting: false,
        semanticState: data.semanticState,
        stateLabel: stateLabels[data.semanticState],
        companionIntent: data.companionIntent,
        answer: companionAnswerView(data, stateLabels[data.semanticState]),
      });
    } catch (error) {
      const semanticState = failureState(error);
      const view = errorView(error, "本次回答未完成，请根据状态稍后再试。");
      this.setData({
        submitting: false,
        semanticState,
        stateLabel: stateLabels[semanticState],
        companionIntent: "reminder",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  retryContext() {
    void this.loadContext();
  },

  runPrimaryInsight() {
    const action = this.data.insightAction;
    if (action === "today") {
      void wx.switchTab({ url: routes.today });
      return;
    }
    const url =
      action === "mastery"
        ? routes.xingceMastery
        : action === "positions"
          ? routes.positions
          : routes.dayAdjustment;
    void wx.navigateTo({ url });
  },

  dismissInsight() {
    this.setData({ insightDismissed: true });
  },

  openToday() {
    void wx.switchTab({ url: routes.today });
  },

  openMastery() {
    void wx.navigateTo({ url: routes.xingceMastery });
  },

  openWeeklyPlan() {
    void wx.navigateTo({ url: routes.weeklyPlan });
  },

  openPositions() {
    void wx.navigateTo({ url: routes.positions });
  },

  handleAction(event: WechatMiniprogram.BaseEvent) {
    const actionIndex = Number(event.currentTarget.dataset.index);
    const action =
      Number.isSafeInteger(actionIndex) && actionIndex >= 0
        ? this.data.answer?.allowedActions[actionIndex]
        : undefined;
    if (!action) return;
    const resolution = resolveCompanionAction(action);
    if (resolution.kind === "show_evidence") {
      void wx.showToast({ title: "依据已列在回答下方", icon: "none" });
      return;
    }
    if (resolution.kind === "refresh_context") {
      this.setData({ answer: null });
      void this.loadContext();
      return;
    }
    if (resolution.kind === "unavailable") {
      void wx.showToast({ title: "这个操作暂时不可用，请刷新后重试", icon: "none" });
      return;
    }
    const navigate = () => {
      if (resolution.target.mode === "switchTab") {
        void wx.switchTab({ url: resolution.target.url });
      } else {
        void wx.navigateTo({ url: resolution.target.url });
      }
    };
    if (resolution.requiresConfirmation) {
      wx.showModal({
        title: "打开操作预览",
        content: "岸岸的建议不会自动修改计划，需要你在下一页再次确认。",
        success: ({ confirm }) => {
          if (confirm) navigate();
        },
      });
      return;
    }
    navigate();
  },
});

function failureState(error: unknown): CompanionAskResponse["semanticState"] {
  if (!(error instanceof ApiClientError)) return "failed";
  if (error.statusCode === 429) return "quota_exhausted";
  if (error.statusCode === 403) return "permission_denied";
  if (error.statusCode === 502 || error.statusCode === 503) return "provider_pending";
  return "failed";
}

function companionContextView(
  profile: LearningBrainProfileVersion,
  dashboard: TodayDashboard | null,
) {
  const evidenceCount =
    profile.evidenceSummary.scoreEvidenceCount +
    profile.evidenceSummary.xingceAttemptCount +
    profile.evidenceSummary.xingceMasteryCount +
    profile.evidenceSummary.essayMasteryCount +
    profile.evidenceSummary.taskExecutionCount +
    (profile.evidenceSummary.externalAnswerCount ?? 0);
  const dueCount =
    (dashboard?.dueReflectionCount ?? 0) +
    (dashboard?.dueVerificationCount ?? 0) +
    profile.decisionSummary.reviewDueCount;
  const activeTaskCount =
    dashboard?.tasks.filter((task) => task.status === "ready" || task.status === "in_progress")
      .length ?? 0;
  const goalName = dashboard?.goalGap.positionName?.trim() ?? "";
  const base = {
    contextGoalLabel: goalName ? "主目标已确认" : "主目标待确认",
    contextTaskLabel: `今日 ${String(activeTaskCount)} 项任务`,
    contextReviewLabel: dueCount > 0 ? `${String(dueCount)} 项待复测` : "暂无到期复测",
    contextCapacityLabel: durationLabel(dashboard?.capacity.availableMinutes),
    contextEvidenceLabel: `${String(evidenceCount)} 条学习记录`,
  };

  if (dueCount > 0) {
    return {
      ...base,
      insightTitle: `${String(dueCount)} 项复测或学习取舍已经到期`,
      insightCopy: "继续学新内容前，先处理到期项，才能判断之前的问题有没有真正修好。",
      insightActionLabel: "查看到期项",
      insightAction: "mastery" as PrimaryInsightAction,
    };
  }
  if (!goalName) {
    return {
      ...base,
      insightTitle: "还没有确认主目标岗位",
      insightCopy: "没有目标岗位时，岸岸只能按通用国考能力安排，无法判断分差和学习取舍。",
      insightActionLabel: "去确认岗位",
      insightAction: "positions" as PrimaryInsightAction,
    };
  }
  if (activeTaskCount > 0) {
    return {
      ...base,
      insightTitle: `今天有 ${String(activeTaskCount)} 项任务可以推进`,
      insightCopy: "岸岸会结合刚完成的作答更新能力；任何计划调整都会先展示变化，再由你确认。",
      insightActionLabel: "查看今天",
      insightAction: "today" as PrimaryInsightAction,
    };
  }
  return {
    ...base,
    insightTitle: "今天还没有形成可执行安排",
    insightCopy: "确认今天可用时间、精力和压力后，岸岸才能生成真实任务。",
    insightActionLabel: "调整今天",
    insightAction: "adjustment" as PrimaryInsightAction,
  };
}

function durationLabel(value: number | null | undefined): string {
  if (value === null || value === undefined) return "可用时间待确认";
  if (value === 0) return "当前没有可用时间";
  const hours = value / 60;
  return `当前可用 ${Number.isInteger(hours) ? String(hours) : hours.toFixed(1)} 小时`;
}
