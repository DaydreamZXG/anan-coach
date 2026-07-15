import {
  EssayIntelligenceApi,
  type EssayArtifactPlanTaskCommand,
  type EssayCurrentReviewEnsureCommand,
  type EssayReviewIntentView,
  type EssayReviewJobView,
  type EssayTaskWorkspace,
} from "../../api/essay-intelligence";
import { ApiClient, ApiClientError } from "../../api/client";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  essayStageCapability,
  inferEssayStageFromMode,
  parseEssayTaskStage,
  type EssayStageCapability,
} from "../../features/essay-task/capabilities";
import {
  answerSourceTypeLabels,
  answerSourceTypeValues,
  buildDraftingCompletionCommand,
  EssayDraftingError,
  initialEssayDraftingDraft,
  promoteToReviewRecovery,
  reflectionCauseOptions,
  reflectionCauseValues,
  sourceContextLabels,
  sourceContextValues,
  timedStatusLabels,
  timedStatusValues,
  type DraftingCompletionReceipt,
  type EssayDraftingDraft,
  type PendingEssayDraftingSubmission,
  type PendingEssayReviewRecovery,
} from "../../features/essay-task/drafting";
import {
  buildArtifactCommand,
  EssayFormError,
  initialEssayFormDrafts,
  newOutlineSectionDraft,
  newPointDraft,
  pointCategoryLabels,
  pointCategoryValues,
  type EssayFormDrafts,
  type MaterialAnnotationDraft,
  type OutlineSectionDraft,
  type PointDraft,
  type ReviewDraft,
} from "../../features/essay-task/forms";
import {
  presentEssayWorkspace,
  stageBlockingReason,
  type FindingView,
  type MaterialSegmentView,
  type WorkspaceView,
} from "../../features/essay-task/presentation";
import {
  presentGroundedReview,
  presentReviewIntent,
  presentReviewJob,
  type ReviewDisplay,
} from "../../features/essay-task/review-presentation";
import {
  EssayTaskPageRuntimeRegistry,
  type EssayTaskPageRuntime,
} from "../../features/essay-task/page-runtime";
import {
  clearLegacyEssayPending,
  clearPendingDraftingSubmission as clearStoredDraftingSubmission,
  clearPendingPlanTaskCompletion as clearStoredPlanTaskCompletion,
  loadPendingDraftingSubmission as loadStoredDraftingSubmission,
  loadPendingPlanTaskCompletion as loadStoredPlanTaskCompletion,
  storePendingDraftingSubmission as storeDraftingSubmission,
  storePendingPlanTaskCompletion as storePlanTaskCompletion,
} from "../../features/essay-task/pending-store";
import { publishLatestTodayDashboard } from "../../features/planning/runtime-state";
import { createIdempotencyKey } from "../../platform/command-id";

type PageStatus = "loading" | "ready" | "error" | "unavailable";
type SubmissionStatus = "idle" | "submitting" | "saved" | "error";

interface EssayTaskPageData {
  status: PageStatus;
  errorTitle: string;
  errorMessage: string;
  capability: EssayStageCapability | null;
  workspace: EssayTaskWorkspace | null;
  view: EssayTaskWorkspaceView | null;
  blockingReason: string;
  canSubmit: boolean;
  draftLocked: boolean;
  showLoginAction: boolean;
  hasSavedEvidence: boolean;
  submissionStatus: SubmissionStatus;
  submissionMessage: string;
  elapsedLabel: string;
  pointCategoryLabels: string[];
  overallTheme: string;
  materialAnnotations: MaterialAnnotationDraft[];
  points: PointPageDraft[];
  thesis: string;
  outlineSections: OutlineSectionPageDraft[];
  review: ReviewDraft;
  drafting: EssayDraftingDraft;
  draftAvailable: boolean;
  sourceContextLabels: string[];
  timedStatusLabels: string[];
  answerSourceTypeLabels: string[];
  reflectionCauseOptions: Array<{ code: string; label: string; checked: boolean }>;
  reviewDisplay: ReviewDisplay;
  canRegenerateReview: boolean;
}

interface PointPageDraft extends PointDraft {
  sourceOptions: Array<MaterialSegmentView & { checked: boolean }>;
}

interface OutlineSectionPageDraft extends OutlineSectionDraft {
  pointOptions: Array<{ pointId: string; label: string; checked: boolean }>;
}

interface FindingPageView extends FindingView {
  acknowledged: boolean;
  priority: boolean;
  priorityDisabled: boolean;
}

interface EssayTaskWorkspaceView extends Omit<WorkspaceView, "findings"> {
  findings: FindingPageView[];
}

interface EssayTaskPageCustom {
  loadWorkspace(): Promise<void>;
  retry(): void;
  exit(): void;
  goLogin(): void;
  openMaterial(): void;
  openAnswerSheet(): void;
  scrollToMaterial(event: WechatMiniprogram.BaseEvent): void;
  inputOverallTheme(event: WechatMiniprogram.Input): void;
  inputMaterialField(event: WechatMiniprogram.Input): void;
  addPoint(): void;
  removePoint(event: WechatMiniprogram.BaseEvent): void;
  changePointCategory(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  inputPointStatement(event: WechatMiniprogram.Input): void;
  changePointSources(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  inputThesis(event: WechatMiniprogram.Input): void;
  addOutlineSection(): void;
  removeOutlineSection(event: WechatMiniprogram.BaseEvent): void;
  inputOutlineField(event: WechatMiniprogram.Input): void;
  changeOutlineSources(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  changeAcknowledgedFindings(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  changePriorityFindings(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  inputLearnerUnderstanding(event: WechatMiniprogram.Input): void;
  inputDraftingField(event: WechatMiniprogram.Input): void;
  changeDraftingSourceContext(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeDraftingTimedStatus(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeDraftingSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeReflectionCauses(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  submitStage(): Promise<void>;
  submitArtifact(): Promise<void>;
  submitDrafting(): Promise<void>;
  resumeDraftingSubmission(): Promise<void>;
  resumeCurrentReview(): Promise<void>;
  pollGroundedReview(): Promise<void>;
  regenerateGroundedReview(): Promise<void>;
  loadExistingReview(reviewVersionId: string): Promise<void>;
}

type EssayTaskPageInstance = WechatMiniprogram.Page.Instance<
  EssayTaskPageData,
  EssayTaskPageCustom
>;

const emptyReview: ReviewDraft = {
  acknowledgedFindingIds: [],
  priorityFindingIds: [],
  learnerUnderstanding: "",
};

const idleReviewDisplay: ReviewDisplay = {
  state: "idle",
  title: "",
  message: "",
  result: null,
};

const pageRuntimes = new EssayTaskPageRuntimeRegistry<EssayTaskPageInstance>();

Page<EssayTaskPageData, EssayTaskPageCustom>({
  data: {
    status: "loading",
    errorTitle: "",
    errorMessage: "",
    capability: null,
    workspace: null,
    view: null,
    blockingReason: "",
    canSubmit: false,
    draftLocked: false,
    showLoginAction: false,
    hasSavedEvidence: false,
    submissionStatus: "idle",
    submissionMessage: "",
    elapsedLabel: "00:00",
    pointCategoryLabels,
    overallTheme: "",
    materialAnnotations: [],
    points: [],
    thesis: "",
    outlineSections: [],
    review: emptyReview,
    drafting: initialEssayDraftingDraft(),
    draftAvailable: true,
    sourceContextLabels,
    timedStatusLabels,
    answerSourceTypeLabels,
    reflectionCauseOptions: reflectionCauseOptions([]),
    reviewDisplay: idleReviewDisplay,
    canRegenerateReview: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const runtime = pageRuntimes.attach(this);
    runtime.activeTaskVersionId = options.taskVersionId?.trim() ?? "";
    runtime.activePlanTaskId = options.planTaskId?.trim() ?? "";
    runtime.basedOnExecutionVersionId = options.basedOnExecutionVersionId?.trim() || undefined;
    runtime.activeStage =
      parseEssayTaskStage(options.stage) ?? inferEssayStageFromMode(options.mode);
    runtime.startedAt = Date.now();
    startTimer(this, runtime);

    if (!runtime.activeStage || !runtime.activeTaskVersionId || !runtime.activePlanTaskId) {
      this.setData({
        status: "error",
        errorTitle: "任务地址不完整",
        errorMessage: "缺少明确的申论学习阶段、题目版本或规划任务，无法原子完成证据与任务结算。",
      });
      return;
    }
    const capability = essayStageCapability(runtime.activeStage);
    this.setData({ capability });
    if (!capability.writable) {
      this.setData({
        status: "unavailable",
        errorTitle: `${capability.title}暂未开放`,
        errorMessage: capability.unavailableReason,
      });
      return;
    }
    if (environment.dataMode === "prototype") {
      this.setData({
        status: "unavailable",
        errorTitle: "演示数据不可写入学习档案",
        errorMessage:
          "开发版当前使用 prototype 数据。申论材料、版本链和提交结果必须来自已认证 API，因此本页不会显示假材料或返回假成功。",
      });
      return;
    }
    const session = currentAuthSession();
    if (!session) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能开始申论任务",
        errorMessage: "学习证据必须绑定你的账号，当前没有可用登录会话。",
        showLoginAction: true,
      });
      return;
    }
    runtime.activeUserId = session.userId;
    clearLegacyEssayPending();
    runtime.pendingCompletion = loadStoredPlanTaskCompletion(session.userId, {
      taskVersionId: runtime.activeTaskVersionId,
      stage: runtime.activeStage,
      planTaskId: runtime.activePlanTaskId,
    });
    runtime.pendingDraftingSubmission =
      runtime.activeStage === "essay_drafting"
        ? loadStoredDraftingSubmission(session.userId, {
            taskVersionId: runtime.activeTaskVersionId,
            planTaskId: runtime.activePlanTaskId,
          })
        : null;
    runtime.api = new EssayIntelligenceApi(
      new ApiClient(environment.apiBaseUrl, session.accessToken),
    );
    void this.loadWorkspace();
  },

  onShow() {
    const runtime = pageRuntimes.get(this);
    if (!runtime) return;
    runtime.pageVisible = true;
    if (runtime.startedAt > 0) startTimer(this, runtime);
    if (this.data.status === "ready" && runtime.activeReviewJobId) {
      scheduleReviewPoll(this, runtime, 0);
    } else if (
      this.data.status === "ready" &&
      runtime.activeReviewIntent &&
      shouldEnsureReviewIntent(runtime.activeReviewIntent)
    ) {
      void this.resumeCurrentReview();
    }
  },

  onHide() {
    const runtime = pageRuntimes.get(this);
    if (!runtime) return;
    runtime.pageVisible = false;
    stopTimer(runtime);
    stopReviewPoll(runtime);
  },

  onUnload() {
    const runtime = pageRuntimes.get(this);
    if (!runtime) return;
    runtime.pageVisible = false;
    stopTimer(runtime);
    stopReviewPoll(runtime);
    pageRuntimes.detach(this);
  },

  async loadWorkspace() {
    const runtime = pageRuntimes.get(this);
    if (!runtime?.api || !runtime.activeStage || !runtime.activeTaskVersionId) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    const api = runtime.api;
    const activeStage = runtime.activeStage;
    const activeTaskVersionId = runtime.activeTaskVersionId;
    this.setData({ status: "loading", errorTitle: "", errorMessage: "" });
    try {
      const workspace = (await api.getWorkspace(activeTaskVersionId)).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const pendingReview = reviewRecovery(runtime.pendingDraftingSubmission);
      runtime.activeReviewIntent = workspace.progression.reviewIntent ?? null;
      runtime.activeReviewJobId =
        runtime.activeReviewIntent?.jobId ??
        pendingReview?.ensure?.jobId ??
        workspace.progression.reviewJobId ??
        "";
      const capability = essayStageCapability(activeStage);
      const draftingStage = activeStage === "essay_drafting";
      const drafts =
        runtime.pendingCompletion?.taskVersionId === activeTaskVersionId &&
        runtime.pendingCompletion.stage === activeStage
          ? runtime.pendingCompletion.drafts
          : initialEssayFormDrafts(workspace);
      const pendingDrafting = runtime.pendingDraftingSubmission;
      const drafting =
        draftingStage &&
        pendingDrafting?.phase === "completion_pending" &&
        pendingDrafting.taskVersionId === activeTaskVersionId
          ? pendingDrafting.draft
          : initialEssayDraftingDraft(workspace);
      const blockingReason = stageBlockingReason(activeStage, workspace);
      const baseView = presentEssayWorkspace(workspace, capability.artifactType);
      const pageDrafts = decorateDrafts(drafts, baseView);
      this.setData({
        status: "ready",
        capability,
        workspace,
        view: pageDrafts.view,
        blockingReason,
        canSubmit:
          capability.writable &&
          (!blockingReason ||
            runtime.pendingCompletion !== null ||
            runtime.pendingDraftingSubmission !== null) &&
          !(
            draftingStage &&
            pendingDrafting?.phase !== "completion_pending" &&
            (pendingReview !== null || runtime.activeReviewIntent !== null)
          ),
        draftLocked:
          runtime.pendingCompletion !== null ||
          runtime.pendingDraftingSubmission !== null ||
          (draftingStage && runtime.activeReviewIntent !== null),
        showLoginAction: false,
        hasSavedEvidence: capability.artifactType
          ? workspace.currentArtifacts[capability.artifactType] !== undefined
          : draftingStage &&
            (Boolean(workspace.progression.answerVersionId) ||
              pendingReview !== null ||
              runtime.activeReviewIntent !== null),
        submissionStatus:
          runtime.pendingCompletion || runtime.pendingDraftingSubmission ? "error" : "idle",
        submissionMessage:
          runtime.pendingCompletion || runtime.pendingDraftingSubmission
            ? "上次命令尚未获得完整终态，本次会沿用已持久化的命令与幂等键继续。"
            : "",
        drafting,
        reflectionCauseOptions: reflectionCauseOptions(drafting.reflectionCauseCodes),
        draftAvailable:
          !draftingStage ||
          (pendingDrafting
            ? pendingDrafting.phase === "completion_pending" &&
              pendingDrafting.completion.command.answer.mode === "create"
            : !workspace.progression.answerVersionId),
        reviewDisplay: idleReviewDisplay,
        canRegenerateReview: false,
        ...pageDrafts.drafts,
      });
      if (draftingStage) {
        if (runtime.pendingDraftingSubmission?.phase === "completion_pending") {
          void this.resumeDraftingSubmission();
        } else if (runtime.activeReviewIntent) {
          applyReviewIntent(this, runtime, runtime.activeReviewIntent);
          if (shouldEnsureReviewIntent(runtime.activeReviewIntent)) {
            void this.resumeCurrentReview();
          } else if (runtime.activeReviewJobId) {
            scheduleReviewPoll(this, runtime, 0);
          }
        } else if (pendingReview) {
          void this.resumeCurrentReview();
        } else if (runtime.activeReviewJobId) {
          scheduleReviewPoll(this, runtime, 0);
        } else if (workspace.progression.reviewVersionId) {
          void this.loadExistingReview(workspace.progression.reviewVersionId);
        }
      }
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        status: "error",
        errorTitle: apiError?.statusCode === 404 ? "题目版本不可用" : "申论工作区加载失败",
        errorMessage: apiError
          ? `${apiError.message}（请求 ${apiError.requestId}）`
          : "工作区没有成功加载，请稍后重试。",
      });
    }
  },

  retry() {
    if (environment.dataMode === "prototype" || !currentAuthSession()) return;
    void this.loadWorkspace();
  },

  exit() {
    void wx.navigateBack();
  },

  goLogin() {
    void wx.navigateTo({ url: routes.login });
  },

  openMaterial() {
    const first = this.data.view?.segments[0];
    if (first) scrollTo(first.anchorId);
  },

  openAnswerSheet() {
    const runtime = pageRuntimes.get(this);
    if (runtime?.activeStage === "essay_drafting" && this.data.draftAvailable) {
      scrollTo("drafting-answer");
    }
  },

  scrollToMaterial(event: WechatMiniprogram.BaseEvent) {
    const anchor = String(event.currentTarget.dataset.anchor ?? "");
    if (anchor) scrollTo(anchor);
  },

  inputOverallTheme(event: WechatMiniprogram.Input) {
    markDraftChanged();
    this.setData({ overallTheme: event.detail.value, submissionStatus: "idle" });
  },

  inputMaterialField(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const field = String(event.currentTarget.dataset.field ?? "");
    const annotations = [...this.data.materialAnnotations];
    const current = annotations[index];
    if (!current) return;
    if (field === "paragraphSummary" || field === "subjectsText" || field === "evidenceQuote") {
      annotations[index] = { ...current, [field]: event.detail.value };
      markDraftChanged();
      this.setData({ materialAnnotations: annotations, submissionStatus: "idle" });
    }
  },

  addPoint() {
    markDraftChanged();
    this.setData({
      points: [...this.data.points, decoratePoint(newPointDraft(), this.data.view?.segments ?? [])],
      submissionStatus: "idle",
    });
  },

  removePoint(event: WechatMiniprogram.BaseEvent) {
    const index = datasetIndex(event);
    markDraftChanged();
    this.setData({
      points: this.data.points.filter((_, itemIndex) => itemIndex !== index),
      submissionStatus: "idle",
    });
  },

  changePointCategory(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = datasetIndex(event);
    const categoryIndex = Number(event.detail.value);
    const category = pointCategoryValues[categoryIndex];
    const current = this.data.points[index];
    if (!current || !category) return;
    const points = [...this.data.points];
    points[index] = { ...current, category, categoryIndex };
    markDraftChanged();
    this.setData({ points, submissionStatus: "idle" });
  },

  inputPointStatement(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const current = this.data.points[index];
    if (!current) return;
    const points = [...this.data.points];
    points[index] = { ...current, statement: event.detail.value };
    markDraftChanged();
    this.setData({ points, submissionStatus: "idle" });
  },

  changePointSources(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    const index = datasetIndex(event);
    const current = this.data.points[index];
    if (!current) return;
    const points = [...this.data.points];
    points[index] = {
      ...current,
      sourceSegmentIds: event.detail.value,
      sourceOptions: current.sourceOptions.map((item) => ({
        ...item,
        checked: event.detail.value.includes(item.segmentId),
      })),
    };
    markDraftChanged();
    this.setData({ points, submissionStatus: "idle" });
  },

  inputThesis(event: WechatMiniprogram.Input) {
    markDraftChanged();
    this.setData({ thesis: event.detail.value, submissionStatus: "idle" });
  },

  addOutlineSection() {
    markDraftChanged();
    this.setData({
      outlineSections: [
        ...this.data.outlineSections,
        decorateOutlineSection(newOutlineSectionDraft(), this.data.view?.pointOptions ?? []),
      ],
      submissionStatus: "idle",
    });
  },

  removeOutlineSection(event: WechatMiniprogram.BaseEvent) {
    const index = datasetIndex(event);
    markDraftChanged();
    this.setData({
      outlineSections: this.data.outlineSections.filter((_, itemIndex) => itemIndex !== index),
      submissionStatus: "idle",
    });
  },

  inputOutlineField(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const field = String(event.currentTarget.dataset.field ?? "");
    const sections = [...this.data.outlineSections];
    const current = sections[index];
    if (!current) return;
    if (field === "heading" || field === "argument" || field === "plannedWordsText") {
      sections[index] = { ...current, [field]: event.detail.value };
      markDraftChanged();
      this.setData({ outlineSections: sections, submissionStatus: "idle" });
    }
  },

  changeOutlineSources(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    const index = datasetIndex(event);
    const sections = [...this.data.outlineSections];
    const current = sections[index];
    if (!current) return;
    sections[index] = {
      ...current,
      sourcePointIds: event.detail.value,
      pointOptions: current.pointOptions.map((item) => ({
        ...item,
        checked: event.detail.value.includes(item.pointId),
      })),
    };
    markDraftChanged();
    this.setData({ outlineSections: sections, submissionStatus: "idle" });
  },

  changeAcknowledgedFindings(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    const acknowledged = event.detail.value;
    const priorities = this.data.review.priorityFindingIds.filter((id) =>
      acknowledged.includes(id),
    );
    markDraftChanged();
    const view = this.data.view
      ? {
          ...this.data.view,
          findings: this.data.view.findings.map((item) => ({
            ...item,
            acknowledged: acknowledged.includes(item.findingId),
            priority: priorities.includes(item.findingId),
            priorityDisabled: !acknowledged.includes(item.findingId),
          })),
        }
      : null;
    this.setData({
      view,
      review: {
        ...this.data.review,
        acknowledgedFindingIds: acknowledged,
        priorityFindingIds: priorities,
      },
      submissionStatus: "idle",
    });
  },

  changePriorityFindings(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    if (event.detail.value.length > 3) {
      void wx.showToast({ title: "最多选择 3 个优先问题", icon: "none" });
      this.setData({ review: { ...this.data.review } });
      return;
    }
    markDraftChanged();
    const view = this.data.view
      ? {
          ...this.data.view,
          findings: this.data.view.findings.map((item) => ({
            ...item,
            priority: event.detail.value.includes(item.findingId),
          })),
        }
      : null;
    this.setData({
      view,
      review: { ...this.data.review, priorityFindingIds: event.detail.value },
      submissionStatus: "idle",
    });
  },

  inputLearnerUnderstanding(event: WechatMiniprogram.Input) {
    markDraftChanged();
    this.setData({
      review: { ...this.data.review, learnerUnderstanding: event.detail.value },
      submissionStatus: "idle",
    });
  },

  inputDraftingField(event: WechatMiniprogram.Input) {
    const field = String(event.currentTarget.dataset.field ?? "");
    if (
      field !== "answerText" &&
      field !== "userThought" &&
      field !== "pointFindingText" &&
      field !== "outlineText" &&
      field !== "elapsedMinutesText" &&
      field !== "whatIWillChange" &&
      field !== "verificationPlan"
    ) {
      return;
    }
    markDraftChanged();
    this.setData({
      drafting: { ...this.data.drafting, [field]: event.detail.value },
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  changeDraftingSourceContext(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = Number(event.detail.value);
    const value = sourceContextValues[index];
    if (!value) return;
    markDraftChanged();
    this.setData({
      drafting: {
        ...this.data.drafting,
        sourceContext: value,
        sourceContextIndex: index,
      },
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  changeDraftingTimedStatus(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = Number(event.detail.value);
    const value = timedStatusValues[index];
    if (!value) return;
    markDraftChanged();
    this.setData({
      drafting: {
        ...this.data.drafting,
        timedStatus: value,
        timedStatusIndex: index,
        ...(value === "timed" ? {} : { elapsedMinutesText: "" }),
      },
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  changeDraftingSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = Number(event.detail.value);
    const value = answerSourceTypeValues[index];
    if (!value) return;
    markDraftChanged();
    this.setData({
      drafting: { ...this.data.drafting, sourceType: value, sourceTypeIndex: index },
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  changeReflectionCauses(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    const values = event.detail.value.filter((value) =>
      reflectionCauseValues.includes(value as (typeof reflectionCauseValues)[number]),
    ) as EssayDraftingDraft["reflectionCauseCodes"];
    markDraftChanged();
    this.setData({
      drafting: { ...this.data.drafting, reflectionCauseCodes: values },
      reflectionCauseOptions: reflectionCauseOptions(values),
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  async submitStage() {
    const runtime = pageRuntimes.get(this);
    if (runtime?.activeStage === "essay_drafting") {
      await this.submitDrafting();
      return;
    }
    await this.submitArtifact();
  },

  async submitDrafting() {
    const runtime = pageRuntimes.get(this);
    if (
      !runtime?.api ||
      runtime.activeStage !== "essay_drafting" ||
      !this.data.workspace ||
      !this.data.canSubmit ||
      this.data.submissionStatus === "submitting"
    ) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    try {
      if (!runtime.pendingDraftingSubmission) {
        const command = buildDraftingCompletionCommand(
          this.data.workspace,
          this.data.drafting,
          Math.max(1, currentElapsedSeconds(runtime)),
          {
            planTaskId: runtime.activePlanTaskId,
            ...(runtime.basedOnExecutionVersionId
              ? { basedOnExecutionVersionId: runtime.basedOnExecutionVersionId }
              : {}),
          },
        );
        const idempotencyKey = await createIdempotencyKey("essay-drafting-complete");
        if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
        runtime.pendingDraftingSubmission = {
          phase: "completion_pending",
          taskVersionId: runtime.activeTaskVersionId,
          planTaskId: runtime.activePlanTaskId,
          draft: this.data.drafting,
          completion: {
            idempotencyKey,
            command,
          },
        };
        storeDraftingSubmission(runtime.activeUserId, runtime.pendingDraftingSubmission);
      }
      await this.resumeDraftingSubmission();
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const message =
        error instanceof EssayDraftingError
          ? error.message
          : "作答命令尚未建立，请检查输入后重试。";
      this.setData({ submissionStatus: "error", submissionMessage: message });
    }
  },

  async resumeDraftingSubmission() {
    const runtime = pageRuntimes.get(this);
    if (
      !runtime?.api ||
      !runtime.pendingDraftingSubmission ||
      runtime.activeStage !== "essay_drafting"
    ) {
      return;
    }
    if (runtime.pendingDraftingSubmission.phase === "review_pending") {
      await this.resumeCurrentReview();
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    const api = runtime.api;
    this.setData({
      submissionStatus: "submitting",
      submissionMessage: pendingDraftingStatusMessage(runtime.pendingDraftingSubmission),
      draftLocked: true,
      canSubmit: false,
    });
    try {
      const completionPending = runtime.pendingDraftingSubmission;
      const result = (
        await api.completeDraftingPlanTask(
          completionPending.completion.command,
          completionPending.completion.idempotencyKey,
        )
      ).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const receipt: DraftingCompletionReceipt = {
        attemptId: result.attempt.attemptId,
        answerVersionId: result.answer.answerVersionId,
        reflectionArtifactVersionId: result.reflection.artifactVersionId,
      };
      const recovery = promoteToReviewRecovery(completionPending, receipt, result.reviewIntent);
      runtime.pendingDraftingSubmission = recovery;
      runtime.activeReviewIntent = result.reviewIntent;
      storeDraftingSubmission(runtime.activeUserId, recovery);
      publishLatestTodayDashboard(result.completion.plan);
      const workspace = this.data.workspace;
      this.setData({
        ...(workspace
          ? {
              workspace: {
                ...workspace,
                progression: {
                  ...workspace.progression,
                  attemptId: receipt.attemptId,
                  answerVersionId: receipt.answerVersionId,
                  reviewIntent: result.reviewIntent,
                },
              },
            }
          : {}),
        hasSavedEvidence: true,
        submissionMessage: "答案、结构化复盘和今日申论任务已原子完成，正在确认 AI 评阅任务…",
        blockingReason: "本次申论作答已进入计划大脑；AI 评阅是后续异步证据，不会回滚已完成任务。",
      });
      await this.resumeCurrentReview();
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      handleDraftingRequestError(this, runtime, error);
    }
  },

  async resumeCurrentReview() {
    const runtime = pageRuntimes.get(this);
    if (!runtime?.api || runtime.activeStage !== "essay_drafting") return;
    const pending = reviewRecovery(runtime.pendingDraftingSubmission);
    const intent = runtime.activeReviewIntent ?? this.data.workspace?.progression.reviewIntent;
    if (!intent) {
      this.setData({
        submissionStatus: "error",
        submissionMessage:
          "服务端没有返回本版答案对应的评阅意图，页面不会退回旧送审链路。请重新加载工作区核对版本状态。",
      });
      return;
    }
    runtime.activeReviewIntent = intent;
    if (!shouldEnsureReviewIntent(intent)) {
      applyReviewIntent(this, runtime, intent);
      if (runtime.activeReviewJobId) scheduleReviewPoll(this, runtime, 0);
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    let ensure = pending?.ensure;
    try {
      if (!ensure) {
        const idempotencyKey = await createIdempotencyKey("essay-review-ensure");
        if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
        ensure = { idempotencyKey };
        if (pending) {
          runtime.pendingDraftingSubmission = { ...pending, ensure };
          storeDraftingSubmission(runtime.activeUserId, runtime.pendingDraftingSubmission);
        }
      }
      this.setData({
        submissionStatus: "submitting",
        submissionMessage: "答案和复盘已保存，正在确认唯一评阅任务…",
        draftLocked: true,
        canSubmit: false,
      });
      const result = (
        await runtime.api.ensureCurrentGroundedReview(
          buildReviewEnsureCommand(intent),
          ensure.idempotencyKey,
        )
      ).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (result.intent.status === "queued" && !result.job) {
        throw new EssayDraftingError(
          "评阅意图已排队，但服务端没有返回对应 job。请重新加载核对状态。",
        );
      }
      runtime.activeReviewIntent = result.intent;
      runtime.activeReviewJobId = result.job?.jobId ?? result.intent.jobId ?? "";
      if (pending) {
        runtime.pendingDraftingSubmission = {
          ...pending,
          intent: {
            intentId: result.intent.intentId,
            intentVersionId: result.intent.intentVersionId,
          },
          ensure: {
            ...ensure,
            ...(runtime.activeReviewJobId ? { jobId: runtime.activeReviewJobId } : {}),
            ...(result.job?.reviewVersionId ? { reviewVersionId: result.job.reviewVersionId } : {}),
          },
        };
        storeDraftingSubmission(runtime.activeUserId, runtime.pendingDraftingSubmission);
      }
      const workspace = this.data.workspace;
      if (workspace) {
        this.setData({
          workspace: {
            ...workspace,
            progression: {
              ...workspace.progression,
              reviewIntent: result.intent,
              reviewJobId: runtime.activeReviewJobId || null,
            },
          },
        });
      }
      if (result.job) applyReviewJob(this, runtime, result.job);
      else applyReviewIntent(this, runtime, result.intent);
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      handleDraftingRequestError(this, runtime, error);
    }
  },

  async pollGroundedReview() {
    const runtime = pageRuntimes.get(this);
    const jobId =
      reviewRecovery(runtime?.pendingDraftingSubmission ?? null)?.ensure?.jobId ??
      runtime?.activeReviewJobId;
    if (!runtime?.api || !jobId || runtime.reviewPollInFlight || !runtime.pageVisible) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    const api = runtime.api;
    runtime.reviewPollInFlight = true;
    stopReviewPoll(runtime);
    try {
      const job = (await api.getGroundedReviewJob(jobId)).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      runtime.activeReviewJobId = job.jobId;
      const pendingReview = reviewRecovery(runtime.pendingDraftingSubmission);
      const ensure = pendingReview?.ensure;
      if (pendingReview && ensure) {
        runtime.pendingDraftingSubmission = {
          ...pendingReview,
          ensure: {
            ...ensure,
            ...(job.reviewVersionId ? { reviewVersionId: job.reviewVersionId } : {}),
          },
        };
        storeDraftingSubmission(runtime.activeUserId, runtime.pendingDraftingSubmission);
      }
      applyReviewJob(this, runtime, job);
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        submissionStatus: "error",
        submissionMessage: apiError
          ? `评阅状态暂未同步：${apiError.message}（请求 ${apiError.requestId}）。已保留 jobId，将继续查询。`
          : "评阅状态暂未同步，已保留 jobId，将继续查询。",
      });
      scheduleReviewPoll(this, runtime, 5_000);
    } finally {
      runtime.reviewPollInFlight = false;
    }
  },

  async regenerateGroundedReview() {
    const runtime = pageRuntimes.get(this);
    if (
      !runtime?.api ||
      !runtime.activeReviewJobId ||
      this.data.submissionStatus === "submitting"
    ) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    const api = runtime.api;
    const sourceJobId = runtime.activeReviewJobId;
    const idempotencyKey = `essay-review-regenerate:${sourceJobId}`;
    stopReviewPoll(runtime);
    this.setData({
      submissionStatus: "submitting",
      submissionMessage: "正在建立新的完整评阅执行版本…",
      canRegenerateReview: false,
    });
    try {
      const job = (await api.regenerateGroundedReview(sourceJobId, idempotencyKey)).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      runtime.activeReviewJobId = job.jobId;
      const pendingReview = reviewRecovery(runtime.pendingDraftingSubmission);
      if (pendingReview?.ensure) {
        runtime.pendingDraftingSubmission = {
          ...pendingReview,
          ensure: {
            ...pendingReview.ensure,
            idempotencyKey,
            jobId: job.jobId,
            ...(job.reviewVersionId ? { reviewVersionId: job.reviewVersionId } : {}),
          },
        };
        storeDraftingSubmission(runtime.activeUserId, runtime.pendingDraftingSubmission);
      }
      applyReviewJob(this, runtime, job);
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        submissionStatus: "error",
        submissionMessage: apiError
          ? `${apiError.message}（请求 ${apiError.requestId}）。原评阅 job 已保留，可安全重试同一重评命令。`
          : "重评命令没有获得完整响应，原评阅 job 已保留，可安全重试。",
        canRegenerateReview: true,
      });
    }
  },

  async loadExistingReview(reviewVersionId: string) {
    const runtime = pageRuntimes.get(this);
    if (!runtime?.api) return;
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    const api = runtime.api;
    try {
      const review = (await api.getGroundedReview(reviewVersionId)).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const display = presentGroundedReview(review);
      this.setData({
        reviewDisplay: display,
        hasSavedEvidence: true,
        draftLocked: true,
        canSubmit: false,
        submissionStatus: display.state === "completed" ? "saved" : "error",
        submissionMessage: display.message,
        canRegenerateReview:
          Boolean(runtime.activeReviewJobId) &&
          (display.state === "blocked" || display.state === "failed"),
      });
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        submissionStatus: "error",
        submissionMessage: apiError
          ? `评阅版本加载失败：${apiError.message}（请求 ${apiError.requestId}）`
          : "评阅版本没有成功加载。",
      });
    }
  },

  async submitArtifact() {
    const runtime = pageRuntimes.get(this);
    if (
      !runtime?.api ||
      !runtime.activeStage ||
      !this.data.workspace ||
      !this.data.canSubmit ||
      this.data.submissionStatus === "submitting"
    ) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    const api = runtime.api;
    const activeStage = runtime.activeStage;
    try {
      const drafts: EssayFormDrafts = {
        overallTheme: this.data.overallTheme,
        materialAnnotations: this.data.materialAnnotations,
        points: this.data.points,
        thesis: this.data.thesis,
        outlineSections: this.data.outlineSections,
        review: this.data.review,
      };
      if (!runtime.pendingCompletion) {
        const artifact = buildArtifactCommand(activeStage, this.data.workspace, drafts);
        const command: EssayArtifactPlanTaskCommand = {
          artifact,
          planTaskId: runtime.activePlanTaskId,
          elapsedMinutes: Math.max(1, Math.ceil(currentElapsedSeconds(runtime) / 60)),
          ...(runtime.basedOnExecutionVersionId
            ? { basedOnExecutionVersionId: runtime.basedOnExecutionVersionId }
            : {}),
        };
        const idempotencyKey = await createIdempotencyKey("essay-complete");
        if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
        runtime.pendingCompletion = {
          taskVersionId: runtime.activeTaskVersionId,
          stage: activeStage,
          planTaskId: runtime.activePlanTaskId,
          idempotencyKey,
          command,
          drafts,
        };
        storePlanTaskCompletion(runtime.activeUserId, runtime.pendingCompletion);
      }
      this.setData({
        submissionStatus: "submitting",
        submissionMessage: "正在原子写入学习证据并结算今日任务…",
        draftLocked: true,
      });
      const result = (
        await api.completePlanTask(
          runtime.pendingCompletion.command,
          runtime.pendingCompletion.idempotencyKey,
        )
      ).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      clearStoredPlanTaskCompletion(runtime.activeUserId, runtime.activePlanTaskId);
      runtime.pendingCompletion = null;
      publishLatestTodayDashboard(result.completion.plan);
      const refreshedWorkspace: EssayTaskWorkspace = {
        ...this.data.workspace,
        currentArtifacts: {
          ...this.data.workspace.currentArtifacts,
          [result.artifact.artifactType]: result.artifact,
        },
      };
      const refreshedDrafts = initialEssayFormDrafts(refreshedWorkspace);
      const refreshedView = decorateDrafts(
        refreshedDrafts,
        presentEssayWorkspace(refreshedWorkspace, result.artifact.artifactType),
      );
      this.setData({
        workspace: refreshedWorkspace,
        view: refreshedView.view,
        ...refreshedView.drafts,
        submissionStatus: "saved",
        draftLocked: false,
        canSubmit: false,
        hasSavedEvidence: true,
        blockingReason: "本阶段证据和今日任务已原子完成，请返回今日页继续新计划。",
        submissionMessage: `已写入 ${result.artifact.artifactType} v${String(result.artifact.versionNo)} 并完成任务结算，版本 ${result.artifact.artifactVersionId.slice(0, 8)}`,
      });
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      const conflict = apiError?.statusCode === 409;
      if (conflict && runtime.pendingCompletion) {
        clearStoredPlanTaskCompletion(runtime.activeUserId, runtime.pendingCompletion.planTaskId);
        runtime.pendingCompletion = null;
      }
      const message =
        error instanceof EssayFormError
          ? error.message
          : apiError?.statusCode === 409
            ? "学习证据或任务执行版本已变化；原子事务没有写入。请返回今日页，按最新任务版本重新进入。"
            : apiError
              ? `${apiError.message}（请求 ${apiError.requestId}）`
              : "学习证据与任务结算未获得完整响应，请重试同一命令。";
      this.setData({
        submissionStatus: "error",
        submissionMessage: message,
        draftLocked: runtime.pendingCompletion !== null,
        canSubmit: conflict ? false : this.data.canSubmit,
        blockingReason: conflict
          ? "当前任务上下文已失效，必须从最新今日计划重新进入，不能覆盖其他设备或页面写入的版本。"
          : this.data.blockingReason,
      });
    }
  },
});

function datasetIndex(event: WechatMiniprogram.BaseEvent): number {
  return Number(event.currentTarget.dataset.index ?? -1);
}

function markDraftChanged(): void {
  // A persisted completion command locks the draft in WXML until its atomic retry succeeds.
}

function scrollTo(anchorId: string): void {
  void wx.pageScrollTo({ selector: `#${anchorId}`, duration: 260 });
}

function startTimer(page: EssayTaskPageInstance, runtime: EssayTaskPageRuntime): void {
  stopTimer(runtime);
  runtime.activeSince = Date.now();
  const update = () => {
    if (!pageRuntimes.owns(page, runtime)) return;
    const elapsedSeconds = currentElapsedSeconds(runtime);
    const minutes = Math.floor(elapsedSeconds / 60);
    const seconds = elapsedSeconds % 60;
    page.setData({
      elapsedLabel: `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`,
    });
  };
  update();
  runtime.timerHandle = setInterval(update, 1000);
}

function stopTimer(runtime: EssayTaskPageRuntime): void {
  if (runtime.timerHandle !== undefined) clearInterval(runtime.timerHandle);
  runtime.timerHandle = undefined;
  if (runtime.activeSince > 0) {
    runtime.activeElapsedMs += Math.max(0, Date.now() - runtime.activeSince);
    runtime.activeSince = 0;
  }
}

function currentElapsedSeconds(runtime: EssayTaskPageRuntime): number {
  const runningMs = runtime.activeSince > 0 ? Math.max(0, Date.now() - runtime.activeSince) : 0;
  return Math.max(0, Math.floor((runtime.activeElapsedMs + runningMs) / 1_000));
}

function pendingDraftingStatusMessage(pending: PendingEssayDraftingSubmission): string {
  if (pending.phase === "completion_pending") {
    return "正在原子写入作答、结构化复盘并完成今日申论任务…";
  }
  if (!pending.ensure?.jobId) return "答案版本已保存，正在确认真实证据化评阅…";
  return "评阅任务已建立，正在查询完整终态…";
}

function buildReviewEnsureCommand(intent: EssayReviewIntentView): EssayCurrentReviewEnsureCommand {
  return {
    taskVersionId: intent.taskVersionId,
    materialBundleVersionId: intent.materialBundleVersionId,
    rubricVersionId: intent.rubricVersionId,
    reviewScope: "grounded_full",
  };
}

function shouldEnsureReviewIntent(intent: EssayReviewIntentView, nowMs = Date.now()): boolean {
  if (intent.status === "pending") return true;
  if (intent.status !== "waiting_quota" || !intent.retryAfter) return false;
  const retryAtMs = new Date(intent.retryAfter).getTime();
  return Number.isFinite(retryAtMs) && retryAtMs <= nowMs;
}

function applyReviewIntent(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  intent: EssayReviewIntentView,
): void {
  if (!pageRuntimes.owns(page, runtime)) return;
  runtime.activeReviewIntent = intent;
  runtime.activeReviewJobId = intent.jobId ?? "";
  const display = presentReviewIntent(intent);
  const waiting = display.state === "waiting_quota";
  const failed = display.state === "failed";
  page.setData({
    reviewDisplay: display,
    submissionStatus: waiting ? "saved" : failed ? "error" : "submitting",
    submissionMessage: display.message,
    draftLocked: true,
    canSubmit: false,
    hasSavedEvidence: true,
    canRegenerateReview: failed && Boolean(runtime.activeReviewJobId),
  });
  if (waiting || failed) stopReviewPoll(runtime);
}

function applyReviewJob(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  job: EssayReviewJobView,
): void {
  if (!pageRuntimes.owns(page, runtime)) return;
  runtime.activeReviewJobId = job.jobId;
  const display = presentReviewJob(job);
  const pending = display.state === "queued" || display.state === "running";
  page.setData({
    reviewDisplay: display,
    submissionStatus: pending ? "submitting" : display.state === "completed" ? "saved" : "error",
    submissionMessage: display.message,
    draftLocked: true,
    canSubmit: false,
    hasSavedEvidence: true,
    canRegenerateReview:
      Boolean(runtime.activeReviewJobId) &&
      (display.state === "blocked" || display.state === "failed"),
  });
  if (pending) {
    scheduleReviewPoll(page, runtime, 2_000);
    return;
  }
  if (display.state === "failed" || display.state === "blocked") {
    stopReviewPoll(runtime);
    return;
  }
  const planTaskId = runtime.pendingDraftingSubmission?.planTaskId;
  if (planTaskId) clearStoredDraftingSubmission(runtime.activeUserId, planTaskId);
  runtime.pendingDraftingSubmission = null;
  stopReviewPoll(runtime);
}

function handleDraftingRequestError(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  error: unknown,
): void {
  if (!pageRuntimes.owns(page, runtime)) return;
  const apiError = error instanceof ApiClientError ? error : null;
  const conflict = apiError?.statusCode === 409;
  const completionPending = runtime.pendingDraftingSubmission?.phase === "completion_pending";
  if (conflict && runtime.pendingDraftingSubmission) {
    clearStoredDraftingSubmission(
      runtime.activeUserId,
      runtime.pendingDraftingSubmission.planTaskId,
    );
    runtime.pendingDraftingSubmission = null;
  }
  const message =
    error instanceof EssayDraftingError
      ? error.message
      : conflict
        ? completionPending
          ? "题目、答案、复盘或计划版本已变化；原子命令没有部分写入。请从最新今日计划重新进入。"
          : "作答与计划已经原子完成，但评阅命令所绑定的版本发生冲突。请返回今日页按最新状态进入。"
        : apiError
          ? `${apiError.message}（请求 ${apiError.requestId}）。${completionPending ? "原子命令与幂等键已保留" : "评阅意图已保存在账户中"}，可安全重试。`
          : `请求没有获得完整响应。${completionPending ? "原子命令与幂等键已保留" : "评阅意图已保存在账户中"}，可安全重试。`;
  page.setData({
    submissionStatus: "error",
    submissionMessage: message,
    draftLocked: runtime.pendingDraftingSubmission !== null,
    canSubmit: !conflict,
    hasSavedEvidence:
      page.data.hasSavedEvidence ||
      runtime.pendingDraftingSubmission?.phase === "review_pending" ||
      runtime.activeReviewIntent !== null,
    blockingReason:
      conflict && completionPending
        ? "当前作答上下文已失效，必须从最新今日计划重新进入。"
        : page.data.blockingReason,
  });
}

function reviewRecovery(
  pending: PendingEssayDraftingSubmission | null,
): PendingEssayReviewRecovery | null {
  return pending?.phase === "review_pending" ? pending : null;
}

function scheduleReviewPoll(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  delayMs: number,
): void {
  stopReviewPoll(runtime);
  if (!pageRuntimes.owns(page, runtime) || !runtime.pageVisible || !runtime.activeReviewJobId) {
    return;
  }
  runtime.reviewPollHandle = setTimeout(() => {
    if (pageRuntimes.owns(page, runtime)) void page.pollGroundedReview();
  }, delayMs);
}

function stopReviewPoll(runtime: EssayTaskPageRuntime): void {
  if (runtime.reviewPollHandle !== undefined) clearTimeout(runtime.reviewPollHandle);
  runtime.reviewPollHandle = undefined;
}

function decorateDrafts(
  drafts: EssayFormDrafts,
  view: WorkspaceView,
): {
  drafts: Omit<EssayFormDrafts, "points" | "outlineSections"> & {
    points: PointPageDraft[];
    outlineSections: OutlineSectionPageDraft[];
  };
  view: EssayTaskWorkspaceView;
} {
  return {
    drafts: {
      ...drafts,
      points: drafts.points.map((item) => decoratePoint(item, view.segments)),
      outlineSections: drafts.outlineSections.map((item) =>
        decorateOutlineSection(item, view.pointOptions),
      ),
    },
    view: {
      ...view,
      findings: view.findings.map((item) => ({
        ...item,
        acknowledged: drafts.review.acknowledgedFindingIds.includes(item.findingId),
        priority: drafts.review.priorityFindingIds.includes(item.findingId),
        priorityDisabled: !drafts.review.acknowledgedFindingIds.includes(item.findingId),
      })),
    },
  };
}

function decoratePoint(point: PointDraft, segments: MaterialSegmentView[]): PointPageDraft {
  return {
    ...point,
    sourceOptions: segments.map((segment) => ({
      ...segment,
      checked: point.sourceSegmentIds.includes(segment.segmentId),
    })),
  };
}

function decorateOutlineSection(
  section: OutlineSectionDraft,
  points: WorkspaceView["pointOptions"],
): OutlineSectionPageDraft {
  return {
    ...section,
    pointOptions: points.map((point) => ({
      ...point,
      checked: section.sourcePointIds.includes(point.pointId),
    })),
  };
}
