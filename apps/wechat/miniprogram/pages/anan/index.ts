import { CompanionApi } from "../../api/companion";
import type {
  CompanionAskRequest,
  CompanionAskResponse,
  LearningBrainProfileVersion,
  TodayDashboard,
} from "../../api/generated/types.gen";
import { LearningBrainApi } from "../../api/learning-brain";
import { PlanningApi } from "../../api/planning";
import { routes } from "../../app/navigation/routes";
import {
  companionAnswerView,
  companionFailureView,
  type CompanionAnswerView,
} from "../../features/companion/presentation";
import { resolveCompanionAction } from "../../features/learning-brain/companion-action";
import { errorView, requireAuthenticatedClient } from "../../features/learning-brain/presentation";
import { ApiClientError } from "../../api/client";
import { stableFingerprint } from "../../platform/command-id";
import { OperationAttemptStore } from "../../platform/operation-attempt-store";
import {
  consumeCompanionLaunchContext,
  type CompanionLaunchContext,
} from "../../features/companion/context-store";
import { currentAuthSession, sameSessionOwner } from "../../auth/session";

const stateLabels: Record<CompanionAskResponse["semanticState"], string> = {
  ready: "已根据当前证据回答",
  evidence_insufficient: "依据不足",
  permission_denied: "当前页面无权回答",
  security_blocked: "已阻断不安全请求",
  quota_exhausted: "今日额度已用完",
  provider_pending: "回答结果待安全确认",
  failed: "本次回答未完成",
};

const companionAskScope = "companion-ask";
const companionAttemptTtlMs = 7 * 24 * 60 * 60 * 1000;
const companionPendingMessage = "本次问题结果待确认，再试会继续确认。";

type PrimaryInsightAction = "mastery" | "positions" | "today" | "adjustment";

function defaultCompanionWorkspace(): CompanionLaunchContext["workspace"] {
  return "training";
}

Page({
  data: {
    contextStatus: "loading",
    manualKeyboard: false,
    keyboardOffset: 0,
    learnerStateVersion: "",
    question: "",
    askedQuestion: "",
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
    const windowWidth = wx.getWindowInfo().windowWidth;
    this.setData({ question: question.slice(0, 800), manualKeyboard: windowWidth >= 768 });
    void this.loadContext();
  },

  onShow() {
    if (typeof this.getTabBar === "function") {
      this.getTabBar().setData({ active: "anan" });
    }
    if (!this.restoreUnfinishedCompanionAttempt()) this.applyLaunchContext();
  },

  restoreUnfinishedCompanionAttempt(): boolean {
    const auth = currentAuthSession();
    if (!auth) return false;
    const attempt = new OperationAttemptStore(auth.userId).load(companionAskScope);
    if (
      !attempt ||
      (attempt.state !== "pending" && attempt.state !== "unknown") ||
      !isCompanionAskRequest(attempt.canonicalInput)
    ) {
      return false;
    }
    if (this.data.submitting) return true;
    const command = attempt.canonicalInput;
    this.setData({
      contextStatus: "ready",
      question: command.questionText,
      askedQuestion: "",
      submitting: false,
      errorMessage: companionPendingMessage,
      requestId: "",
      semanticState: "provider_pending",
      stateLabel: stateLabels.provider_pending,
      companionIntent: "reminder",
      answer: null,
      contextBannerVisible: false,
      contextSourceLabel: "",
      contextTitle: "",
      contextSummary: "",
      askRouteId: command.context.routeId,
      askWorkspace: command.context.workspace,
      askObjectRefs: (command.context.objectRefs ?? []).map((item) => ({ ...item })),
      askTaskMode: command.context.taskMode ?? undefined,
      askTaskStage: command.context.taskStage ?? undefined,
      learnerStateVersion: command.context.learnerStateVersion,
    });
    return true;
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

  onKeyboardHeight(event: WechatMiniprogram.TextareaKeyboardHeightChange) {
    if (!this.data.manualKeyboard) return;
    const height = event.detail.height;
    // fixed 定位的输入坞在 iPad 上不随 adjust-position 上移，手动抬升。
    this.setData({ keyboardOffset: height > 0 ? height : 0 });
  },

  inputQuestion(event: WechatMiniprogram.Input) {
    this.setData({ question: event.detail.value });
  },

  choosePrompt(event: WechatMiniprogram.BaseEvent) {
    const question = String(event.currentTarget.dataset.question ?? "").trim();
    if (question) this.setData({ question });
  },

  async ask() {
    const auth = currentAuthSession();
    const client = requireAuthenticatedClient();
    if (!auth || !client || this.data.submitting || this.data.contextStatus !== "ready") return;
    const question = this.data.question.trim();
    if (!/[\u3400-\u9fffA-Za-z0-9]/u.test(question)) {
      this.setData({ errorMessage: "写一个词或一句话，让岸岸知道你想问什么。" });
      return;
    }
    const currentCommand = companionAskCommand(question, {
      routeId: this.data.askRouteId,
      workspace: this.data.askWorkspace,
      objectRefs: this.data.askObjectRefs,
      taskMode: this.data.askTaskMode,
      taskStage: this.data.askTaskStage,
      learnerStateVersion: this.data.learnerStateVersion,
    });
    this.setData({
      submitting: true,
      askedQuestion: question,
      question: "",
      errorMessage: "",
      requestId: "",
      answer: null,
      companionIntent: "thinking",
      stateLabel: "正在检查权限并检索当前依据",
    });
    // 聊天语义：问题气泡滚动到视口顶部，回答在其下方展开。
    void wx
      .pageScrollTo({ selector: "#ask-exchange", offsetTop: -12, duration: 240 })
      .catch(() => undefined);
    const attempts = new OperationAttemptStore(auth.userId);
    let attempt: Awaited<ReturnType<OperationAttemptStore["begin"]>> | undefined;
    try {
      const command = companionCommandForRetry(attempts, currentCommand);
      cancelChangedCompanionAttempt(attempts, command);
      attempt = await attempts.begin({
        scope: companionAskScope,
        canonicalInput: command,
        idempotencyKeyPrefix: "companion",
        expiresAtMs: Date.now() + companionAttemptTtlMs,
      });
      const { data } = await new CompanionApi(client).ask(command, attempt.idempotencyKey);
      attempts.markSucceeded(companionAskScope, attempt.idempotencyKey);
      if (!sameSessionOwner(currentAuthSession(), auth)) return;
      this.setData({
        submitting: false,
        semanticState: data.semanticState,
        stateLabel: stateLabels[data.semanticState],
        companionIntent: data.companionIntent,
        answer: companionAnswerView(data, stateLabels[data.semanticState]),
      });
    } catch (error) {
      const unknownOutcome = isTransportUnknownOutcome(error);
      if (attempt) {
        if (unknownOutcome)
          attempts.markTransportUnknown(companionAskScope, attempt.idempotencyKey);
        else attempts.markTerminal(companionAskScope, attempt.idempotencyKey);
      }
      if (!sameSessionOwner(currentAuthSession(), auth)) return;
      const view = unknownOutcome ? transportUnknownView(error) : companionFailureView(error);
      this.setData({
        submitting: false,
        semanticState: view.semanticState,
        stateLabel: stateLabels[view.semanticState],
        companionIntent: "reminder",
        errorMessage: view.message,
        requestId: view.requestId,
      });
    }
  },

  restoreAskedQuestion() {
    const question = this.data.askedQuestion.trim();
    if (!question) return;
    this.setData({
      question,
      askedQuestion: "",
      errorMessage: "",
      requestId: "",
      semanticState: "ready",
      stateLabel: "可以修改问题后重新发送",
      companionIntent: "companion",
    });
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

function companionAskCommand(
  questionText: string,
  context: {
    routeId: string;
    workspace: CompanionAskRequest["context"]["workspace"];
    objectRefs: NonNullable<CompanionAskRequest["context"]["objectRefs"]>;
    taskMode: CompanionAskRequest["context"]["taskMode"] | undefined;
    taskStage: CompanionAskRequest["context"]["taskStage"] | undefined;
    learnerStateVersion: string;
  },
): CompanionAskRequest {
  return {
    questionText,
    context: {
      routeId: context.routeId,
      workspace: context.workspace,
      objectRefs: [...context.objectRefs],
      ...(context.taskMode && context.taskStage
        ? { taskMode: context.taskMode, taskStage: context.taskStage }
        : {}),
      learnerStateVersion: context.learnerStateVersion,
    },
  };
}

function cancelChangedCompanionAttempt(
  attempts: OperationAttemptStore,
  canonicalInput: CompanionAskRequest,
): void {
  const existing = attempts.load(companionAskScope);
  if (
    existing &&
    (existing.state === "pending" || existing.state === "unknown") &&
    existing.canonicalFingerprint !== stableFingerprint(canonicalInput)
  ) {
    attempts.cancel(companionAskScope, existing.idempotencyKey);
  }
}

function companionCommandForRetry(
  attempts: OperationAttemptStore,
  currentCommand: CompanionAskRequest,
): CompanionAskRequest {
  const existing = attempts.load(companionAskScope);
  if (
    !existing ||
    (existing.state !== "pending" && existing.state !== "unknown") ||
    !isCompanionAskRequest(existing.canonicalInput) ||
    existing.canonicalInput.questionText !== currentCommand.questionText ||
    !sameUserSelectedCompanionContext(existing.canonicalInput.context, currentCommand.context)
  ) {
    return currentCommand;
  }
  return existing.canonicalInput;
}

function sameUserSelectedCompanionContext(
  persisted: CompanionAskRequest["context"],
  current: CompanionAskRequest["context"],
): boolean {
  return (
    persisted.routeId === current.routeId &&
    persisted.workspace === current.workspace &&
    persisted.taskMode === current.taskMode &&
    persisted.taskStage === current.taskStage &&
    stableFingerprint(persisted.objectRefs ?? []) === stableFingerprint(current.objectRefs ?? [])
  );
}

function isTransportUnknownOutcome(error: unknown): boolean {
  return !(
    error instanceof ApiClientError &&
    error.classification === "http_response" &&
    error.statusCode >= 400 &&
    error.statusCode < 500 &&
    error.statusCode !== 408
  );
}

function transportUnknownView(error: unknown): {
  semanticState: CompanionAskResponse["semanticState"];
  message: string;
  requestId: string;
} {
  return {
    semanticState: "provider_pending",
    message: companionPendingMessage,
    requestId: error instanceof ApiClientError ? error.requestId : "",
  };
}

function isCompanionAskRequest(value: unknown): value is CompanionAskRequest {
  if (!hasExactKeys(value, ["context", "questionText"])) return false;
  if (
    !isBoundedTrimmedString(value.questionText, 800) ||
    !/[\u3400-\u9fffA-Za-z0-9]/u.test(value.questionText)
  ) {
    return false;
  }
  const context = value.context;
  if (!isPlainObject(context)) return false;
  const hasTaskMode = Object.prototype.hasOwnProperty.call(context, "taskMode");
  const hasTaskStage = Object.prototype.hasOwnProperty.call(context, "taskStage");
  if (hasTaskMode !== hasTaskStage) return false;
  const expectedKeys = ["learnerStateVersion", "objectRefs", "routeId", "workspace"];
  if (hasTaskMode) expectedKeys.push("taskMode", "taskStage");
  if (!hasExactKeys(context, expectedKeys)) return false;
  if (
    !isBoundedTrimmedString(context.routeId, 200) ||
    !isBoundedTrimmedString(context.learnerStateVersion, 200) ||
    !isCompanionWorkspace(context.workspace) ||
    !Array.isArray(context.objectRefs) ||
    context.objectRefs.length > 12 ||
    !context.objectRefs.every(isCompanionObjectRef)
  ) {
    return false;
  }
  const identities = context.objectRefs.map((item) => stableFingerprint(item));
  if (new Set(identities).size !== identities.length) return false;
  return (
    !hasTaskMode ||
    (isCompanionTaskMode(context.taskMode) && isCompanionTaskStage(context.taskStage))
  );
}

function isCompanionObjectRef(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["objectRefs"]>[number] {
  if (!hasExactKeys(value, ["id", "type", "version"])) return false;
  return (
    isCompanionObjectType(value.type) &&
    isBoundedTrimmedString(value.id, 200) &&
    isBoundedTrimmedString(value.version, 200)
  );
}

function isCompanionWorkspace(
  value: unknown,
): value is CompanionAskRequest["context"]["workspace"] {
  return value === "today" || value === "training" || value === "position" || value === "profile";
}

function isCompanionTaskMode(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["taskMode"]> {
  return value === "practice" || value === "retest" || value === "mock" || value === "essay";
}

function isCompanionTaskStage(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["taskStage"]> {
  return (
    value === "reading" || value === "answering" || value === "submitted" || value === "reviewing"
  );
}

function isCompanionObjectType(
  value: unknown,
): value is NonNullable<CompanionAskRequest["context"]["objectRefs"]>[number]["type"] {
  return (
    value === "position" ||
    value === "question" ||
    value === "material" ||
    value === "attempt" ||
    value === "plan" ||
    value === "report"
  );
}

function isBoundedTrimmedString(value: unknown, maxLength: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    value.trim() === value
  );
}

function hasExactKeys(
  value: unknown,
  expectedKeys: readonly string[],
): value is Record<string, unknown> {
  if (!isPlainObject(value)) return false;
  const keys = Object.keys(value);
  return (
    keys.length === expectedKeys.length &&
    expectedKeys.every((key) => Object.prototype.hasOwnProperty.call(value, key))
  );
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype: unknown = Object.getPrototypeOf(value) as unknown;
  return prototype === Object.prototype || prototype === null;
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
