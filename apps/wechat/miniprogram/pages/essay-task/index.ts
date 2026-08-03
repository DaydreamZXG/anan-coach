import {
  EssayIntelligenceApi,
  type EssayArtifactPlanTaskCommand,
  type EssayCurrentReviewEnsureCommand,
  type EssayReviewIntentView,
  type EssayReviewJobView,
  type EssayReviewResponse,
  type EssayRewriteResultV2,
  type EssayTaskWorkspace,
} from "../../api/essay-intelligence";
import { ApiClient, ApiClientError } from "../../api/client";
import { LearningMediaApi } from "../../api/learning-media";
import { EssayHandwritingApi } from "../../api/essay-handwriting";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../../app/navigation/safe-back";
import { currentAuthSession, type ActiveAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  essayStageCapability,
  inferEssayStageFromMode,
  parseEssayWriterContext,
  parseEssayTaskStage,
  type EssayStageCapability,
  type EssayTaskStage,
} from "../../features/essay-task/capabilities";
import {
  buildRewriteCommand,
  buildRewritePlanTaskCompletionCommand,
  closureSourceTypeLabels,
  closureSourceTypeValues,
  criterionOptions,
  EssayClosureError,
  findingDecisionLabels,
  findingDecisionValues,
  findingResolutionLabels,
  findingResolutionValues,
  initialRewriteDraft,
  initialTransferDraft,
  masterySummary,
  acceptedFindingIdsFromWorkspace,
  type CriterionOption,
  type EssayRewriteDraft,
  type EssayTransferDraft,
} from "../../features/essay-task/closure";
import {
  advanceEssayTransferFlow,
  pollEssayTransferReview,
  transferPhaseMessage,
} from "../../features/essay-task/closure-flow";
import {
  clearEssayClosureDraft,
  loadEssayClosureDraft,
  storeEssayClosureDraft,
} from "../../features/essay-task/closure-store";
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
  clearEssayDraftingDraft,
  loadEssayDraftingDraft,
  storeEssayDraftingDraft,
} from "../../features/essay-task/drafting-store";
import {
  buildArtifactCommand,
  EssayFormError,
  initialEssayFormDrafts,
  newOutlineSectionDraft,
  newPointDraft,
  pointCategoryLabels,
  pointCategoryValues,
  reviewDecisionValues,
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
import {
  validateVoiceCollection,
  type ReflectionVoiceSegment,
} from "../../features/learning-media/contracts";
import {
  clearReflectionLearningMediaDraft,
  loadReflectionLearningMediaDraft,
  storeReflectionLearningMediaDraft,
} from "../../features/learning-media/draft-store";
import {
  deleteLocalVoice,
  ensureRecordPermission,
  persistRecordedVoice,
  requestRecordSetting,
  WechatVoicePreview,
  WechatVoiceRecorder,
} from "../../features/learning-media/platform";
import {
  presentReflectionVoiceSegments,
  presentReflectionVoiceSummary,
  type ReflectionVoiceSegmentView,
  type ReflectionVoiceSummaryView,
} from "../../features/learning-media/presentation";
import { ReflectionVoiceUploadCoordinator } from "../../features/learning-media/upload-controller";
import {
  canonicalHandwritingText,
  confirmedHandwritingBinding,
  confirmHandwritingSpan,
  correctionSpans,
  editHandwritingSpan,
  emptyHandwritingState,
  EssayHandwritingError,
  needsRecognitionCorrection,
  reviewingHandwritingState,
  type EssayHandwritingState,
} from "../../features/essay-handwriting/workflow";
import {
  chooseSourceImage,
  ensureCameraPermission,
  requestCameraSetting,
} from "../../platform/source-image";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";

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
  submissionRequestId: string;
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
  rewriteDraft: EssayRewriteDraft | null;
  transferDraft: EssayTransferDraft;
  closureReview: EssayReviewResponse | null;
  criterionOptions: CriterionOption[];
  closureSourceTypeLabels: string[];
  findingResolutionLabels: string[];
  findingDecisionLabels: string[];
  closureResult: ClosureResultView | null;
  voiceSegments: ReflectionVoiceSegment[];
  voiceSegmentViews: ReflectionVoiceSegmentView[];
  voiceSummary: ReflectionVoiceSummaryView;
  voiceRecording: boolean;
  voicePlayingSegmentId: string;
  handwriting: EssayHandwritingState;
  workbenchLinks: Array<{ label: string; url: string }>;
}

interface ClosureResultView {
  statusLabel: string;
  statusTone: "success" | "warning" | "danger";
  versionId: string;
  masteryLabel: string;
  planningMessage: string;
}

interface PointPageDraft extends PointDraft {
  sourceOptions: Array<MaterialSegmentView & { checked: boolean }>;
}

interface OutlineSectionPageDraft extends OutlineSectionDraft {
  pointOptions: Array<{ pointId: string; label: string; checked: boolean }>;
}

interface FindingPageView extends FindingView {
  decision: ReviewDraft["decisions"][number]["decision"];
  decisionIndex: number;
  learnerReason: string;
  followUpQuestion: string;
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
  openMaterialAt(event: WechatMiniprogram.BaseEvent): void;
  openMaterialPage(anchorId: string): void;
  openAnswerSheet(): void;
  openDraftingNotes(): void;
  askAnanAboutEssay(): void;
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
  changeReviewFindingDecision(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  inputReviewFindingDecisionField(event: WechatMiniprogram.Input): void;
  changePriorityFindings(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  inputLearnerUnderstanding(event: WechatMiniprogram.Input): void;
  inputDraftingField(event: WechatMiniprogram.Input): void;
  changeDraftingSourceContext(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeDraftingTimedStatus(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeDraftingSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  chooseHandwritingImage(event: WechatMiniprogram.BaseEvent): Promise<void>;
  inputHandwritingSpan(event: WechatMiniprogram.Input): void;
  toggleHandwritingSpan(event: WechatMiniprogram.BaseEvent): void;
  confirmHandwriting(): Promise<void>;
  openWorkbench(event: WechatMiniprogram.BaseEvent): void;
  changeReflectionCauses(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  submitStage(): Promise<void>;
  submitArtifact(): Promise<void>;
  submitDrafting(): Promise<void>;
  resumeDraftingSubmission(): Promise<void>;
  resumeCurrentReview(): Promise<void>;
  pollGroundedReview(): Promise<void>;
  regenerateGroundedReview(): Promise<void>;
  loadExistingReview(reviewVersionId: string): Promise<void>;
  loadClosureContext(): Promise<void>;
  inputRewriteField(event: WechatMiniprogram.Input): void;
  changeRewriteSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeRewriteResolution(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  inputRewriteEvidence(event: WechatMiniprogram.Input): void;
  inputTransferField(event: WechatMiniprogram.Input): void;
  changeTransferSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  changeTransferDecision(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  inputTransferDecisionField(event: WechatMiniprogram.Input): void;
  changeTransferCriteria(event: WechatMiniprogram.CustomEvent<{ value: string[] }>): void;
  submitRewrite(): Promise<void>;
  submitTransfer(): Promise<void>;
  resumeTransfer(): Promise<void>;
  pollTransferReview(): Promise<void>;
  startVoiceRecording(): Promise<void>;
  stopVoiceRecording(): void;
  uploadVoiceSegments(): Promise<void>;
  playVoiceSegment(event: WechatMiniprogram.BaseEvent): void;
  removeVoiceSegment(event: WechatMiniprogram.BaseEvent): Promise<void>;
}

type EssayTaskPageInstance = WechatMiniprogram.Page.Instance<
  EssayTaskPageData,
  EssayTaskPageCustom
>;

const emptyReview: ReviewDraft = {
  decisions: [],
  priorityFindingIds: [],
  learnerUnderstanding: "",
};

const idleReviewDisplay: ReviewDisplay = {
  state: "idle",
  title: "",
  message: "",
  result: null,
};

const emptyVoiceSummary = presentReflectionVoiceSummary([]);

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
    submissionRequestId: "",
    elapsedLabel: "00:00:00",
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
    rewriteDraft: null,
    transferDraft: initialTransferDraft(),
    closureReview: null,
    criterionOptions: [],
    closureSourceTypeLabels,
    findingResolutionLabels,
    findingDecisionLabels,
    closureResult: null,
    voiceSegments: [],
    voiceSegmentViews: [],
    voiceSummary: emptyVoiceSummary,
    voiceRecording: false,
    voicePlayingSegmentId: "",
    handwriting: emptyHandwritingState(),
    workbenchLinks: [],
  },

  onLoad(options: Record<string, string | undefined>) {
    const runtime = pageRuntimes.attach(this);
    runtime.activeTaskVersionId = options.taskVersionId?.trim() ?? "";
    runtime.activePlanTaskId = options.planTaskId?.trim() ?? "";
    runtime.basedOnExecutionVersionId = options.basedOnExecutionVersionId?.trim() || undefined;
    runtime.activeStage =
      parseEssayTaskStage(options.stage) ?? inferEssayStageFromMode(options.mode);
    runtime.writerContext = runtime.activeStage
      ? parseEssayWriterContext(runtime.activeStage, options)
      : null;
    runtime.returnUrl = essayTaskReturnUrl(options, runtime.activeStage);
    runtime.startedAt = Date.now();

    if (!runtime.activeStage || !runtime.activeTaskVersionId || !runtime.activePlanTaskId) {
      this.setData({
        status: "error",
        errorTitle: "任务地址不完整",
        errorMessage: "页面地址不完整，请从今日计划或申论工作台重新进入。",
      });
      return;
    }
    const capability = essayStageCapability(runtime.activeStage, runtime.writerContext);
    this.setData({ capability });
    if (!capability.writable) {
      this.setData({
        status: "unavailable",
        errorTitle: `${capability.title}当前不能开始`,
        errorMessage: capability.unavailableReason,
      });
      return;
    }
    const session = currentAuthSession();
    if (!session) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能开始申论任务",
        errorMessage: "学习记录必须绑定你的账号，请重新登录后再进入。",
        showLoginAction: true,
      });
      return;
    }
    bindEssaySession(this, runtime, session);
    startTimer(this, runtime);
    void this.loadWorkspace();
  },

  onShow() {
    const runtime = pageRuntimes.get(this);
    if (!runtime) return;
    const session = currentAuthSession();
    if (!session) {
      if (runtime.api) enterEssayAuthRequired(this, runtime);
      return;
    }
    if (
      runtime.activeUserId !== session.userId ||
      runtime.activeAccessToken !== session.accessToken ||
      runtime.activeSessionGeneration !== session.generation
    ) {
      bindEssaySession(this, runtime, session);
      runtime.pageVisible = true;
      startTimer(this, runtime);
      void this.loadWorkspace();
      return;
    }
    if (!runtime.api) return;
    runtime.pageVisible = true;
    if (runtime.startedAt > 0) startTimer(this, runtime);
    if (this.data.status === "ready" && runtime.activeReviewJobId) {
      scheduleReviewPoll(this, runtime, 0);
    } else if (
      this.data.status === "ready" &&
      runtime.activeStage === "essay_transfer" &&
      this.data.transferDraft.phase === "review_queued"
    ) {
      scheduleTransferPoll(this, runtime, 0);
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
    flushDraftingAutosave(this, runtime);
    runtime.pageVisible = false;
    stopTimer(runtime);
    stopReviewPoll(runtime);
    runtime.voiceRecorder?.stop();
    runtime.voicePreview?.stop();
  },

  onUnload() {
    const runtime = pageRuntimes.get(this);
    if (!runtime) return;
    flushDraftingAutosave(this, runtime);
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
      const capability = essayStageCapability(activeStage, runtime.writerContext);
      const draftingStage = activeStage === "essay_drafting";
      const closureStage = activeStage === "essay_rewrite" || activeStage === "essay_transfer";
      if (
        activeStage === "essay_transfer" &&
        runtime.writerContext?.kind === "transfer" &&
        workspace.material.materialBundleVersionId !==
          runtime.writerContext.targetMaterialBundleVersionId
      ) {
        throw new EssayClosureError("当前材料与计划指定的新题不一致，不能用其他材料代替迁移题。");
      }
      const drafts =
        runtime.pendingCompletion?.taskVersionId === activeTaskVersionId &&
        runtime.pendingCompletion.stage === activeStage
          ? runtime.pendingCompletion.drafts
          : initialEssayFormDrafts(workspace);
      const pendingDrafting = runtime.pendingDraftingSubmission;
      const storedDrafting =
        draftingStage && !pendingDrafting && !workspace.progression.answerVersionId
          ? loadEssayDraftingDraft(runtime.activeUserId, {
              taskVersionId: activeTaskVersionId,
              planTaskId: runtime.activePlanTaskId,
            })
          : null;
      if (draftingStage && workspace.progression.answerVersionId) {
        clearEssayDraftingDraft(runtime.activeUserId, runtime.activePlanTaskId);
      }
      const drafting =
        draftingStage &&
        pendingDrafting?.phase === "completion_pending" &&
        pendingDrafting.taskVersionId === activeTaskVersionId
          ? pendingDrafting.draft
          : (storedDrafting ?? initialEssayDraftingDraft(workspace));
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
          !closureStage &&
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
            ? "上次保存结果还不确定，本次会恢复原操作继续确认。"
            : "",
        drafting,
        reflectionCauseOptions: reflectionCauseOptions(drafting.reflectionCauseCodes),
        workbenchLinks: essayWorkbenchLinks(workspace, runtime.activePlanTaskId),
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
        const voiceDraft = loadReflectionLearningMediaDraft(
          runtime.activeUserId,
          voiceDraftScope(runtime.activePlanTaskId),
        );
        applyVoiceSegments(this, voiceDraft.voiceSegments);
      }
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
      } else if (closureStage) {
        void this.loadClosureContext();
      }
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        status: "error",
        errorTitle: apiError?.statusCode === 404 ? "题目内容不可用" : "申论任务加载失败",
        errorMessage: apiError ? apiError.message : "当前申论任务没有成功加载，请稍后重试。",
      });
    }
  },

  openWorkbench(event: WechatMiniprogram.BaseEvent) {
    const url = String(event.currentTarget.dataset.url ?? "");
    if (url) void wx.navigateTo({ url });
  },

  retry() {
    const runtime = pageRuntimes.get(this);
    const session = currentAuthSession();
    if (!runtime || !session) {
      if (runtime) enterEssayAuthRequired(this, runtime);
      return;
    }
    if (
      runtime.activeUserId !== session.userId ||
      runtime.activeAccessToken !== session.accessToken ||
      runtime.activeSessionGeneration !== session.generation
    ) {
      bindEssaySession(this, runtime, session);
    }
    if (!runtime.api) return;
    void this.loadWorkspace();
  },

  exit() {
    navigateBackOr(routes.today);
  },

  goLogin() {
    const returnUrl = pageRuntimes.get(this)?.returnUrl;
    void wx.reLaunch({
      url: returnUrl ? loginUrlForReturn(returnUrl) : routes.login,
    });
  },

  openMaterial() {
    const firstMaterialOrder = this.data.view?.directory[0]?.materialOrder;
    this.openMaterialPage(
      firstMaterialOrder === undefined ? "" : `material-${String(firstMaterialOrder)}`,
    );
  },

  openMaterialAt(event: WechatMiniprogram.BaseEvent) {
    this.openMaterialPage(String(event.currentTarget.dataset.anchor ?? ""));
  },

  openMaterialPage(anchorId: string) {
    const materialUrl = this.data.workbenchLinks.find((item) => item.label === "材料地图")?.url;
    if (!materialUrl) return;
    const anchorQuery = anchorId ? `&anchor=${encodeURIComponent(anchorId)}` : "";
    void wx.navigateTo({
      url: `${materialUrl}${anchorQuery}&insertMode=drafting`,
      events: {
        quoteMaterial: (payload: { text?: string }) => {
          const text = payload.text?.trim() ?? "";
          if (!text || this.data.draftLocked || !this.data.draftAvailable) return;
          markDraftChanged(this);
          this.setData({
            drafting: {
              ...this.data.drafting,
              answerText: appendDraftText(this.data.drafting.answerText, `“${text}”`),
            },
            submissionStatus: "idle",
            submissionMessage: "",
          });
        },
        addMaterialToOutline: (payload: { text?: string }) => {
          const text = payload.text?.trim() ?? "";
          if (!text || this.data.draftLocked || !this.data.draftAvailable) return;
          markDraftChanged(this);
          this.setData({
            drafting: {
              ...this.data.drafting,
              outlineText: appendDraftText(this.data.drafting.outlineText, text),
            },
            submissionStatus: "idle",
            submissionMessage: "",
          });
        },
      },
    });
  },

  openAnswerSheet() {
    const runtime = pageRuntimes.get(this);
    if (runtime?.activeStage === "essay_drafting" && this.data.draftAvailable) {
      scrollTo("drafting-answer");
    }
  },

  openDraftingNotes() {
    scrollTo("drafting-notes");
  },

  askAnanAboutEssay() {
    const auth = currentAuthSession();
    const workspace = this.data.workspace;
    const capability = this.data.capability;
    if (!auth || !workspace || !capability || !workspace.progression.attemptId) return;
    const reviewReady = Boolean(workspace.progression.reviewVersionId);
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: reviewReady ? "申论证据化评阅" : "申论已提交作答",
      title: capability.title,
      summary: `${this.data.view?.questionTypeLabel ?? "申论"} · ${String(workspace.task.wordLimit)} 字 · ${reviewReady ? "评阅依据已就绪" : "等待评阅或复盘"}`,
      suggestedQuestion: reviewReady
        ? "结合这道申论题的材料、我的答案和证据化评阅，告诉我最应该先改哪一处，并定位依据。"
        : "结合这道题的材料和我已经提交的答案，帮我检查目前最需要补充的复盘证据。",
      routeId: "pages/essay-task/index",
      workspace: "training",
      objectRefs: [
        {
          type: "question",
          id: workspace.task.taskKey,
          version: workspace.task.taskVersionId,
        },
      ],
      taskMode: "essay",
      taskStage: reviewReady ? "reviewing" : "submitted",
    });
    void wx.switchTab({ url: routes.anan });
  },

  scrollToMaterial(event: WechatMiniprogram.BaseEvent) {
    const anchor = String(event.currentTarget.dataset.anchor ?? "");
    if (anchor) scrollTo(anchor);
  },

  inputOverallTheme(event: WechatMiniprogram.Input) {
    markDraftChanged(this);
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
      markDraftChanged(this);
      this.setData({ materialAnnotations: annotations, submissionStatus: "idle" });
    }
  },

  addPoint() {
    markDraftChanged(this);
    this.setData({
      points: [...this.data.points, decoratePoint(newPointDraft(), this.data.view?.segments ?? [])],
      submissionStatus: "idle",
    });
  },

  removePoint(event: WechatMiniprogram.BaseEvent) {
    const index = datasetIndex(event);
    if (!this.data.points[index]) return;
    void (async () => {
      const confirm = await wx.showModal({
        title: "删除这个要点？",
        content: "这个要点将从提纲中移除。",
        confirmText: "确认删除",
      });
      if (!confirm.confirm) return;
      markDraftChanged(this);
      this.setData({
        points: this.data.points.filter((_, itemIndex) => itemIndex !== index),
        submissionStatus: "idle",
      });
    })();
  },

  changePointCategory(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = datasetIndex(event);
    const categoryIndex = Number(event.detail.value);
    const category = pointCategoryValues[categoryIndex];
    const current = this.data.points[index];
    if (!current || !category) return;
    const points = [...this.data.points];
    points[index] = { ...current, category, categoryIndex };
    markDraftChanged(this);
    this.setData({ points, submissionStatus: "idle" });
  },

  inputPointStatement(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const current = this.data.points[index];
    if (!current) return;
    const points = [...this.data.points];
    points[index] = { ...current, statement: event.detail.value };
    markDraftChanged(this);
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
    markDraftChanged(this);
    this.setData({ points, submissionStatus: "idle" });
  },

  inputThesis(event: WechatMiniprogram.Input) {
    markDraftChanged(this);
    this.setData({ thesis: event.detail.value, submissionStatus: "idle" });
  },

  addOutlineSection() {
    markDraftChanged(this);
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
    if (!this.data.outlineSections[index]) return;
    void (async () => {
      const confirm = await wx.showModal({
        title: "删除这段提纲？",
        content: "这段提纲将被移除。",
        confirmText: "确认删除",
      });
      if (!confirm.confirm) return;
      markDraftChanged(this);
      this.setData({
        outlineSections: this.data.outlineSections.filter((_, itemIndex) => itemIndex !== index),
        submissionStatus: "idle",
      });
    })();
  },

  inputOutlineField(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const field = String(event.currentTarget.dataset.field ?? "");
    const sections = [...this.data.outlineSections];
    const current = sections[index];
    if (!current) return;
    if (field === "heading" || field === "argument" || field === "plannedWordsText") {
      sections[index] = { ...current, [field]: event.detail.value };
      markDraftChanged(this);
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
    markDraftChanged(this);
    this.setData({ outlineSections: sections, submissionStatus: "idle" });
  },

  changeReviewFindingDecision(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = datasetIndex(event);
    const current = this.data.review.decisions[index];
    const decisionIndex = Number(event.detail.value);
    const decision = reviewDecisionValues[decisionIndex];
    if (!current || decision === undefined) return;
    const decisions = [...this.data.review.decisions];
    decisions[index] = { ...current, decision, decisionIndex };
    const priorities = this.data.review.priorityFindingIds.filter((id) =>
      decisions.some((item) => item.findingId === id && item.decision === "accepted"),
    );
    markDraftChanged(this);
    const view = this.data.view
      ? {
          ...this.data.view,
          findings: this.data.view.findings.map((item) => ({
            ...item,
            ...(item.findingId === current.findingId
              ? {
                  decision,
                  decisionIndex,
                  learnerReason: current.learnerReason,
                  followUpQuestion: current.followUpQuestion,
                }
              : {}),
            priority: priorities.includes(item.findingId),
            priorityDisabled: !decisions.some(
              (draft) => draft.findingId === item.findingId && draft.decision === "accepted",
            ),
          })),
        }
      : null;
    this.setData({
      view,
      review: {
        ...this.data.review,
        decisions,
        priorityFindingIds: priorities,
      },
      submissionStatus: "idle",
    });
  },

  inputReviewFindingDecisionField(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const field = String(event.currentTarget.dataset.field ?? "");
    const current = this.data.review.decisions[index];
    if (!current || (field !== "learnerReason" && field !== "followUpQuestion")) return;
    const decisions = [...this.data.review.decisions];
    decisions[index] = { ...current, [field]: event.detail.value };
    const view = this.data.view
      ? {
          ...this.data.view,
          findings: this.data.view.findings.map((item) =>
            item.findingId === current.findingId ? { ...item, [field]: event.detail.value } : item,
          ),
        }
      : null;
    markDraftChanged(this);
    this.setData({
      view,
      review: { ...this.data.review, decisions },
      submissionStatus: "idle",
    });
  },

  changePriorityFindings(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    if (event.detail.value.length > 3) {
      void wx.showToast({ title: "最多选择 3 个优先问题", icon: "none" });
      this.setData({ review: { ...this.data.review } });
      return;
    }
    markDraftChanged(this);
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
    markDraftChanged(this);
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
    markDraftChanged(this);
    const invalidatesOcr =
      field === "answerText" && this.data.drafting.sourceType === "ocr_confirmed";
    this.setData({
      drafting: {
        ...this.data.drafting,
        [field]: event.detail.value,
        ...(invalidatesOcr ? { sourceArtifactVersionId: "", recognitionRevisionId: "" } : {}),
      },
      ...(invalidatesOcr
        ? {
            handwriting: {
              ...this.data.handwriting,
              phase: "review" as const,
              message: "正文已改动，请重新逐段核对识别文字。",
            },
          }
        : {}),
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  changeDraftingSourceContext(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = Number(event.detail.value);
    const value = sourceContextValues[index];
    if (!value) return;
    markDraftChanged(this);
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
    markDraftChanged(this);
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
    markDraftChanged(this);
    this.setData({
      drafting: {
        ...this.data.drafting,
        sourceType: value,
        sourceTypeIndex: index,
        ...(value === "typed" ? { sourceArtifactVersionId: "", recognitionRevisionId: "" } : {}),
      },
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  async chooseHandwritingImage(event: WechatMiniprogram.BaseEvent) {
    const runtime = pageRuntimes.get(this);
    if (!runtime?.essayHandwritingApi || this.data.handwriting.phase === "uploading") return;
    const source = event.currentTarget.dataset.source === "camera" ? "camera" : "album";
    try {
      if (source === "camera") {
        let permission = await ensureCameraPermission();
        if (permission === "denied" && (await requestCameraSetting())) permission = "granted";
        if (permission !== "granted") {
          throw new EssayHandwritingError("未获得相机权限，没有上传任何手写答案。");
        }
      }
      const file = await chooseSourceImage(source, "申论手写答案.jpg");
      if (!file) return;
      this.setData({
        handwriting: {
          ...emptyHandwritingState(),
          phase: "uploading",
          localImagePath: file.localPath,
          message: "正在安全上传原图并执行手写识别…",
        },
      });
      const idempotencyKey = await createIdempotencyKey("essay-handwriting");
      const revision = await runtime.essayHandwritingApi.createRecognition(
        file,
        idempotencyKey,
        (progress) => {
          if (!pageRuntimes.owns(this, runtime)) return;
          this.setData({
            handwriting: {
              ...this.data.handwriting,
              progressPercent: progress.percent,
            },
          });
        },
      );
      if (!pageRuntimes.owns(this, runtime)) return;
      this.setData({ handwriting: reviewingHandwritingState(revision, file.localPath) });
    } catch (error) {
      if (!pageRuntimes.owns(this, runtime)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      this.setData({
        handwriting: {
          ...this.data.handwriting,
          phase: "error",
          message: handwritingErrorMessage(error),
        },
      });
    }
  },

  inputHandwritingSpan(event: WechatMiniprogram.Input) {
    const spanId = String(event.currentTarget.dataset.spanId ?? "");
    if (!spanId) return;
    this.setData({
      handwriting: editHandwritingSpan(this.data.handwriting, spanId, event.detail.value),
    });
  },

  toggleHandwritingSpan(event: WechatMiniprogram.BaseEvent) {
    const spanId = String(event.currentTarget.dataset.spanId ?? "");
    if (!spanId) return;
    this.setData({
      handwriting: confirmHandwritingSpan(this.data.handwriting, spanId),
    });
  },

  async confirmHandwriting() {
    const runtime = pageRuntimes.get(this);
    const sourceRevision = this.data.handwriting.revision;
    if (!runtime?.essayHandwritingApi || !sourceRevision) return;
    try {
      const spans = correctionSpans(this.data.handwriting);
      const answerText = canonicalHandwritingText(this.data.handwriting);
      this.setData({
        handwriting: {
          ...this.data.handwriting,
          phase: "confirming",
          message: "正在保存你核对后的手写文字…",
        },
      });
      let candidate = sourceRevision;
      if (needsRecognitionCorrection(this.data.handwriting)) {
        candidate = await runtime.essayHandwritingApi.correctRecognition(
          sourceRevision,
          spans,
          await createIdempotencyKey("essay-handwriting-correct"),
        );
      }
      const confirmed = await runtime.essayHandwritingApi.confirmRecognition(
        candidate,
        await createIdempotencyKey("essay-handwriting-confirm"),
      );
      if (!pageRuntimes.owns(this, runtime)) return;
      const binding = confirmedHandwritingBinding(confirmed);
      if (runtime.activeStage === "essay_drafting") {
        this.setData({
          drafting: {
            ...this.data.drafting,
            answerText,
            sourceType: "ocr_confirmed",
            sourceTypeIndex: 1,
            ...binding,
          },
        });
        markDraftChanged(this);
      } else if (runtime.activeStage === "essay_rewrite" && this.data.rewriteDraft) {
        const rewriteDraft = {
          ...this.data.rewriteDraft,
          answerText,
          sourceType: "ocr_confirmed" as const,
          sourceTypeIndex: 1,
          ...binding,
        };
        persistRewriteDraft(this, rewriteDraft, false);
        this.setData({ rewriteDraft });
      } else if (runtime.activeStage === "essay_transfer") {
        const transferDraft = {
          ...this.data.transferDraft,
          answerText,
          sourceType: "ocr_confirmed" as const,
          sourceTypeIndex: 1,
          ...binding,
        };
        persistTransferDraft(this, transferDraft);
        this.setData({ transferDraft });
      }
      this.setData({
        handwriting: {
          ...this.data.handwriting,
          revision: confirmed,
          phase: "confirmed",
          message: "原图、机器识别、你的修订和确认内容已经保存完整。",
        },
        submissionStatus: "idle",
        submissionMessage: "已将核对后的手写文字填入当前答案。",
      });
    } catch (error) {
      if (!pageRuntimes.owns(this, runtime)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      this.setData({
        handwriting: {
          ...this.data.handwriting,
          phase: "error",
          message: handwritingErrorMessage(error),
        },
      });
    }
  },

  changeReflectionCauses(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    const values = event.detail.value.filter((value) =>
      reflectionCauseValues.includes(value as (typeof reflectionCauseValues)[number]),
    ) as EssayDraftingDraft["reflectionCauseCodes"];
    markDraftChanged(this);
    this.setData({
      drafting: { ...this.data.drafting, reflectionCauseCodes: values },
      reflectionCauseOptions: reflectionCauseOptions(values),
      submissionStatus: "idle",
      submissionMessage: "",
    });
  },

  async loadClosureContext() {
    const runtime = pageRuntimes.get(this);
    const workspace = this.data.workspace;
    if (!runtime?.api || !runtime.writerContext || !workspace) return;
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    try {
      if (runtime.writerContext.kind === "rewrite") {
        const context = runtime.writerContext;
        const review = (await runtime.api.getGroundedReview(context.baseReviewVersionId)).data;
        if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
        if (
          review.status !== "completed" ||
          review.attemptId !== context.attemptId ||
          review.answerVersionId !== context.baseAnswerVersionId ||
          context.basedOnAnswerVersionId !== context.baseAnswerVersionId
        ) {
          throw new EssayClosureError("计划指定的原作答、原答案与完整评阅不是同一条记录。");
        }
        const acceptedFindingIds = acceptedFindingIdsFromWorkspace(
          workspace,
          context.findingDecisionArtifactVersionId,
          context.baseReviewVersionId,
        );
        const stored = runtime.closureDraft?.kind === "rewrite" ? runtime.closureDraft : null;
        const rewriteDraft = stored?.draft ?? initialRewriteDraft(review, acceptedFindingIds);
        if (
          rewriteDraft.resolutions.length !== acceptedFindingIds.length ||
          rewriteDraft.resolutions.some((item) => !acceptedFindingIds.includes(item.findingId))
        ) {
          throw new EssayClosureError("本地改写草稿与当前确认的问题不一致，请重新进入。");
        }
        runtime.rewriteReview = review;
        this.setData({
          closureReview: review,
          rewriteDraft,
          canSubmit: !stored?.atomicCommand && !stored?.terminalConflict,
          draftLocked: Boolean(stored?.atomicCommand),
          submissionStatus: stored?.idempotencyKey ? "error" : "idle",
          submissionMessage: stored?.terminalConflict
            ? "这次改写所依据的计划或评阅已更新，请从今日计划重新进入。"
            : stored?.idempotencyKey
              ? "上次改写结果还不确定，将恢复原操作继续确认。"
              : "",
        });
        if (stored?.atomicCommand && !stored.terminalConflict) void this.submitRewrite();
        return;
      }

      const context = runtime.writerContext;
      if (
        workspace.task.taskVersionId !== context.targetTaskVersionId ||
        workspace.material.materialBundleVersionId !== context.targetMaterialBundleVersionId
      ) {
        throw new EssayClosureError("新题或新材料与当前计划不一致，请重新进入。");
      }
      const mastery = (await runtime.api.getMastery()).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      const sourceItems = mastery.items.filter(
        (item) =>
          item.state === "transfer_due" &&
          item.rewriteVersionId === context.sourceRewriteVersionId &&
          item.findingDecisionArtifactVersionId === context.sourceFindingDecisionArtifactVersionId,
      );
      const sourceReviewVersionIds = [
        ...new Set(sourceItems.map((item) => item.sourceReviewVersionId)),
      ];
      if (!sourceItems.length || sourceReviewVersionIds.length !== 1) {
        throw new EssayClosureError(
          "源改写没有唯一的 transfer_due 掌握头，不能猜测要验证的源评阅。",
        );
      }
      const sourceReviewVersionId = sourceReviewVersionIds[0];
      if (!sourceReviewVersionId) {
        throw new EssayClosureError("没有找到上一次完整评阅，请从今日计划重新进入。");
      }
      const sourceReview = (await runtime.api.getGroundedReview(sourceReviewVersionId)).data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (sourceReview.status !== "completed") {
        throw new EssayClosureError("上一次评阅还没有完整完成，暂时不能进行迁移比较。");
      }
      runtime.transferSourceReview = sourceReview;
      const stored = runtime.closureDraft?.kind === "transfer" ? runtime.closureDraft : null;
      const transferDraft = stored?.draft ?? initialTransferDraft();
      let transferReview: EssayReviewResponse | null = null;
      if (transferDraft.reviewVersionId) {
        transferReview = (await runtime.api.getGroundedReview(transferDraft.reviewVersionId)).data;
        if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
        runtime.transferReview = transferReview;
      }
      this.setData({
        transferDraft,
        closureReview: transferReview,
        criterionOptions: transferReview
          ? criterionOptions(sourceReview, transferReview, transferDraft.selectedCriterionCodes)
          : [],
        canSubmit: ["drafting", "review_ready", "decision_saved"].includes(transferDraft.phase),
        draftLocked: !["drafting", "review_ready"].includes(transferDraft.phase),
        submissionStatus: stored ? "error" : "idle",
        submissionMessage:
          transferDraft.phase === "context_invalid"
            ? "这次迁移练习所依据的计划或评阅已更新，请从今日计划重新进入。"
            : stored
              ? "已恢复这个账号上次保存的新题练习进度。"
              : "",
      });
      if (["attempt_saved", "answer_saved", "decision_saved"].includes(transferDraft.phase)) {
        void this.resumeTransfer();
      } else if (transferDraft.phase === "review_queued") {
        scheduleTransferPoll(this, runtime, 0);
      }
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      this.setData({
        canSubmit: false,
        submissionStatus: "error",
        submissionMessage: closureErrorMessage(error),
        blockingReason: closureErrorMessage(error),
      });
    }
  },

  inputRewriteField(event: WechatMiniprogram.Input) {
    const draft = this.data.rewriteDraft;
    const field = String(event.currentTarget.dataset.field ?? "");
    if (
      !draft ||
      !["answerText", "userThought", "pointFindingText", "outlineText"].includes(field)
    ) {
      return;
    }
    const rewriteDraft = {
      ...draft,
      [field]: event.detail.value,
      ...(field === "answerText" && draft.sourceType === "ocr_confirmed"
        ? { sourceArtifactVersionId: "", recognitionRevisionId: "" }
        : {}),
    };
    this.setData({ rewriteDraft, submissionStatus: "idle", submissionMessage: "" });
    persistRewriteDraft(this, rewriteDraft, false);
  },

  changeRewriteSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const draft = this.data.rewriteDraft;
    const index = Number(event.detail.value);
    const sourceType = closureSourceTypeValues[index];
    if (!draft || !sourceType) return;
    const rewriteDraft = {
      ...draft,
      sourceType,
      sourceTypeIndex: index,
      ...(sourceType === "typed" ? { sourceArtifactVersionId: "", recognitionRevisionId: "" } : {}),
    };
    this.setData({ rewriteDraft, submissionStatus: "idle", submissionMessage: "" });
    persistRewriteDraft(this, rewriteDraft, false);
  },

  changeRewriteResolution(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const draft = this.data.rewriteDraft;
    const index = datasetIndex(event);
    const statusIndex = Number(event.detail.value);
    const status = findingResolutionValues[statusIndex];
    if (!draft || status === undefined || !draft.resolutions[index]) return;
    const resolutions = [...draft.resolutions];
    resolutions[index] = {
      ...resolutions[index],
      status,
      statusIndex,
    } as (typeof resolutions)[number];
    const rewriteDraft = { ...draft, resolutions };
    this.setData({ rewriteDraft, submissionStatus: "idle", submissionMessage: "" });
    persistRewriteDraft(this, rewriteDraft, false);
  },

  inputRewriteEvidence(event: WechatMiniprogram.Input) {
    const draft = this.data.rewriteDraft;
    const index = datasetIndex(event);
    if (!draft || !draft.resolutions[index]) return;
    const resolutions = [...draft.resolutions];
    resolutions[index] = {
      ...resolutions[index],
      learnerEvidence: event.detail.value,
    } as (typeof resolutions)[number];
    const rewriteDraft = { ...draft, resolutions };
    this.setData({ rewriteDraft, submissionStatus: "idle", submissionMessage: "" });
    persistRewriteDraft(this, rewriteDraft, false);
  },

  inputTransferField(event: WechatMiniprogram.Input) {
    const field = String(event.currentTarget.dataset.field ?? "");
    if (
      ![
        "answerText",
        "userThought",
        "pointFindingText",
        "outlineText",
        "elapsedMinutesText",
        "learnerUnderstanding",
      ].includes(field)
    ) {
      return;
    }
    const transferDraft = {
      ...this.data.transferDraft,
      [field]: event.detail.value,
      ...(field === "answerText" && this.data.transferDraft.sourceType === "ocr_confirmed"
        ? { sourceArtifactVersionId: "", recognitionRevisionId: "" }
        : {}),
    };
    this.setData({ transferDraft, submissionStatus: "idle", submissionMessage: "" });
    persistTransferDraft(this, transferDraft);
  },

  changeTransferSourceType(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = Number(event.detail.value);
    const sourceType = closureSourceTypeValues[index];
    if (!sourceType) return;
    const transferDraft = {
      ...this.data.transferDraft,
      sourceType,
      sourceTypeIndex: index,
      ...(sourceType === "typed" ? { sourceArtifactVersionId: "", recognitionRevisionId: "" } : {}),
    };
    this.setData({ transferDraft, submissionStatus: "idle", submissionMessage: "" });
    persistTransferDraft(this, transferDraft);
  },

  changeTransferDecision(event: WechatMiniprogram.CustomEvent<{ value: string }>) {
    const index = datasetIndex(event);
    const decisionIndex = Number(event.detail.value);
    const decision = findingDecisionValues[decisionIndex];
    const current = this.data.transferDraft.decisions[index];
    if (decision === undefined || !current) return;
    const decisions = [...this.data.transferDraft.decisions];
    decisions[index] = { ...current, decision, decisionIndex };
    const transferDraft = { ...this.data.transferDraft, decisions };
    this.setData({ transferDraft, submissionStatus: "idle", submissionMessage: "" });
    persistTransferDraft(this, transferDraft);
  },

  inputTransferDecisionField(event: WechatMiniprogram.Input) {
    const index = datasetIndex(event);
    const field = String(event.currentTarget.dataset.field ?? "");
    const current = this.data.transferDraft.decisions[index];
    if (!current || (field !== "learnerReason" && field !== "followUpQuestion")) return;
    const decisions = [...this.data.transferDraft.decisions];
    decisions[index] = { ...current, [field]: event.detail.value };
    const transferDraft = { ...this.data.transferDraft, decisions };
    this.setData({ transferDraft, submissionStatus: "idle", submissionMessage: "" });
    persistTransferDraft(this, transferDraft);
  },

  changeTransferCriteria(event: WechatMiniprogram.CustomEvent<{ value: string[] }>) {
    const selectedCriterionCodes = event.detail.value;
    const allowed = this.data.criterionOptions.map((item) => item.criterionCode);
    if (selectedCriterionCodes.some((code) => !allowed.includes(code))) return;
    const transferDraft = { ...this.data.transferDraft, selectedCriterionCodes };
    this.setData({
      transferDraft,
      criterionOptions: this.data.criterionOptions.map((item) => ({
        ...item,
        checked: selectedCriterionCodes.includes(item.criterionCode),
      })),
      submissionStatus: "idle",
      submissionMessage: "",
    });
    persistTransferDraft(this, transferDraft);
  },

  async submitRewrite() {
    const runtime = pageRuntimes.get(this);
    const draft = this.data.rewriteDraft;
    if (
      !runtime?.api ||
      runtime.writerContext?.kind !== "rewrite" ||
      !runtime.rewriteReview ||
      !this.data.workspace ||
      !draft ||
      this.data.submissionStatus === "submitting"
    ) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    try {
      const existing = runtime.closureDraft?.kind === "rewrite" ? runtime.closureDraft : null;
      let atomicCommand = existing?.atomicCommand;
      let idempotencyKey = existing?.idempotencyKey;
      if (!atomicCommand || !idempotencyKey) {
        const acceptedFindingIds = acceptedFindingIdsFromWorkspace(
          this.data.workspace,
          runtime.writerContext.findingDecisionArtifactVersionId,
          runtime.writerContext.baseReviewVersionId,
        );
        const rewrite = buildRewriteCommand(runtime.writerContext, draft, acceptedFindingIds);
        atomicCommand = buildRewritePlanTaskCompletionCommand(
          rewrite,
          runtime.activePlanTaskId,
          Math.max(1, Math.ceil(currentElapsedSeconds(runtime) / 60)),
          runtime.basedOnExecutionVersionId,
        );
        idempotencyKey = await createIdempotencyKey("essay-rewrite-complete-plan-task");
        if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
        runtime.closureDraft = {
          kind: "rewrite",
          taskVersionId: runtime.activeTaskVersionId,
          planTaskId: runtime.activePlanTaskId,
          writerContext: runtime.writerContext,
          draft,
          idempotencyKey,
          atomicCommand,
        };
        storeEssayClosureDraft(runtime.activeUserId, runtime.closureDraft);
      }
      this.setData({
        submissionStatus: "submitting",
        submissionMessage: "正在保存新答案、修正记录和学习状态…",
        draftLocked: true,
        canSubmit: false,
      });
      const result = (await runtime.api.completeRewritePlanTask(atomicCommand, idempotencyKey))
        .data;
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      publishLatestTodayDashboard(result.completion.plan);
      clearEssayClosureDraft(runtime.activeUserId, runtime.activePlanTaskId);
      runtime.closureDraft = null;
      applyRewriteResult(this, result);
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      const terminalConflict = isTerminalClosureConflict(error);
      if (terminalConflict && runtime.closureDraft?.kind === "rewrite") {
        runtime.closureDraft = { ...runtime.closureDraft, terminalConflict: true };
        storeEssayClosureDraft(runtime.activeUserId, runtime.closureDraft);
      }
      this.setData({
        submissionStatus: "error",
        submissionMessage: terminalConflict
          ? "这次改写所依据的内容已更新，本次没有保存任何不完整内容。请从今日计划重新进入。"
          : closureErrorMessage(error),
        draftLocked:
          runtime.closureDraft?.kind === "rewrite" && Boolean(runtime.closureDraft.atomicCommand),
        canSubmit: !terminalConflict,
        ...(terminalConflict
          ? { blockingReason: "当前改写上下文已失效，不会循环重试旧命令。" }
          : {}),
      });
    }
  },

  async submitTransfer() {
    await this.resumeTransfer();
  },

  async resumeTransfer() {
    const runtime = pageRuntimes.get(this);
    const workspace = this.data.workspace;
    if (
      !runtime?.api ||
      runtime.writerContext?.kind !== "transfer" ||
      !workspace ||
      this.data.submissionStatus === "submitting"
    ) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    let draft = this.data.transferDraft;
    try {
      this.setData({
        submissionStatus: "submitting",
        submissionMessage: transferPhaseMessage(draft.phase),
        draftLocked: true,
        canSubmit: false,
      });
      const flow = await advanceEssayTransferFlow({
        api: runtime.api,
        workspace,
        writerContext: runtime.writerContext,
        draft,
        planTaskId: runtime.activePlanTaskId,
        ...(runtime.basedOnExecutionVersionId
          ? { basedOnExecutionVersionId: runtime.basedOnExecutionVersionId }
          : {}),
        elapsedSeconds: Math.max(1, currentElapsedSeconds(runtime)),
        allowedCriterionCodes: this.data.criterionOptions.map((item) => item.criterionCode),
        createIdempotencyKey,
        persist: (nextDraft) => {
          draft = nextDraft;
          persistTransferDraft(this, nextDraft);
        },
        isActive: () => pageRuntimes.isCurrent(this, runtime, requestEpoch),
      });
      if (flow.state === "stale") return;
      draft = flow.draft;
      if (flow.state === "awaiting_review") {
        this.setData({
          transferDraft: draft,
          submissionStatus: "submitting",
          submissionMessage: "新题答案已保存，完整评阅正在排队。",
        });
        scheduleTransferPoll(this, runtime, 0);
        return;
      }
      publishLatestTodayDashboard(flow.result.completion.plan);
      clearEssayClosureDraft(runtime.activeUserId, runtime.activePlanTaskId);
      runtime.closureDraft = null;
      applyTransferCompletion(this, {
        status: flow.result.transfer.status,
        versionId: flow.result.transfer.verificationVersionId,
        masteryLabel: masterySummary(flow.result.masteryVersions),
      });
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      const terminalConflict = isTerminalClosureConflict(error);
      if (terminalConflict) {
        draft = { ...draft, phase: "context_invalid" };
        persistTransferDraft(this, draft);
      }
      this.setData({
        transferDraft: draft,
        submissionStatus: "error",
        submissionMessage: terminalConflict
          ? "这次迁移练习所依据的内容已更新，本次没有保存任何不完整内容。请从今日计划重新进入。"
          : closureErrorMessage(error),
        draftLocked: !["drafting", "review_ready"].includes(draft.phase),
        canSubmit: ["drafting", "review_ready", "decision_saved"].includes(draft.phase),
        ...(terminalConflict
          ? { blockingReason: "当前迁移上下文已失效，不会循环重试旧命令。" }
          : {}),
      });
    }
  },

  async pollTransferReview() {
    const runtime = pageRuntimes.get(this);
    const draft = this.data.transferDraft;
    if (
      !runtime?.api ||
      runtime.activeStage !== "essay_transfer" ||
      draft.phase !== "review_queued" ||
      !draft.reviewJobId ||
      runtime.reviewPollInFlight ||
      !runtime.pageVisible
    ) {
      return;
    }
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    runtime.reviewPollInFlight = true;
    stopReviewPoll(runtime);
    try {
      const sourceReview = runtime.transferSourceReview;
      if (!sourceReview) throw new EssayClosureError("源评阅尚未加载。");
      const poll = await pollEssayTransferReview(runtime.api, draft, sourceReview);
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (poll.state === "pending") {
        this.setData({
          submissionStatus: "submitting",
          submissionMessage:
            poll.job.status === "queued"
              ? "新题评阅正在排队。"
              : "新题评阅正在核对答案与材料证据。",
        });
        scheduleTransferPoll(this, runtime, 2_000);
        return;
      }
      runtime.transferReview = poll.review;
      persistTransferDraft(this, poll.draft);
      this.setData({
        transferDraft: poll.draft,
        closureReview: poll.review,
        criterionOptions: poll.criterionOptions,
        submissionStatus: "idle",
        submissionMessage: "新题评阅已完成，请逐条做决定并明确选择本次验证维度。",
        draftLocked: false,
        canSubmit: true,
      });
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      this.setData({
        submissionStatus: "error",
        submissionMessage: closureErrorMessage(error),
        canSubmit: false,
        draftLocked: true,
      });
    } finally {
      runtime.reviewPollInFlight = false;
    }
  },

  async startVoiceRecording() {
    const runtime = pageRuntimes.get(this);
    if (!runtime?.voiceRecorder || this.data.voiceRecording || !this.data.voiceSummary.canRecord) {
      return;
    }
    let permission = await ensureRecordPermission();
    if (permission === "denied" && (await requestRecordSetting())) permission = "granted";
    if (permission !== "granted") {
      this.setData({
        submissionStatus: "error",
        submissionMessage: "未获得麦克风权限，没有建立任何假录音。",
      });
      return;
    }
    this.setData({ voiceRecording: true, submissionMessage: "正在录制这次复盘…" });
    runtime.voiceRecorder.start(
      (recorded) => {
        void (async () => {
          try {
            const localPath = await persistRecordedVoice(recorded.tempFilePath);
            if (!pageRuntimes.owns(this, runtime)) return;
            const segmentId = await createIdempotencyKey("essay-reflection-voice");
            if (!pageRuntimes.owns(this, runtime)) return;
            const segment: ReflectionVoiceSegment = {
              segmentId,
              localPath,
              originalFilename: `reflection-${segmentId.slice(-20)}.mp3`,
              mimeType: recorded.mimeType,
              byteSize: recorded.byteSize,
              durationMilliseconds: recorded.durationMilliseconds,
              state: "local",
            };
            const segments = [...this.data.voiceSegments, segment];
            validateVoiceCollection(segments);
            persistVoiceSegments(runtime, segments);
            applyVoiceSegments(this, segments);
            this.setData({
              voiceRecording: false,
              submissionStatus: "idle",
              submissionMessage: "录音已保存为本地草稿，提交前需上传并通过安全检查。",
            });
          } catch (error) {
            if (!pageRuntimes.owns(this, runtime)) return;
            this.setData({
              voiceRecording: false,
              submissionStatus: "error",
              submissionMessage: closureErrorMessage(error),
            });
          }
        })();
      },
      (error) => {
        if (!pageRuntimes.owns(this, runtime)) return;
        this.setData({
          voiceRecording: false,
          submissionStatus: "error",
          submissionMessage: error.message,
        });
      },
    );
  },

  stopVoiceRecording() {
    pageRuntimes.get(this)?.voiceRecorder?.stop();
  },

  async uploadVoiceSegments() {
    const runtime = pageRuntimes.get(this);
    if (!runtime?.voiceUploadCoordinator || !this.data.voiceSegments.length) return;
    const requestEpoch = pageRuntimes.beginRequest(runtime);
    this.setData({
      submissionStatus: "submitting",
      submissionMessage: "正在上传复盘录音并等待安全检查…",
    });
    try {
      const segments = await runtime.voiceUploadCoordinator.uploadAll(
        this.data.voiceSegments,
        (next) => {
          if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
          persistVoiceSegments(runtime, next);
          applyVoiceSegments(this, next);
        },
        () => pageRuntimes.isCurrent(this, runtime, requestEpoch),
      );
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      persistVoiceSegments(runtime, segments);
      applyVoiceSegments(this, segments);
      const failed = segments.some((segment) => segment.state !== "available");
      this.setData({
        submissionStatus: failed ? "error" : "idle",
        submissionMessage: failed
          ? "有录音尚未上传完成，本地草稿和上传进度已保留。"
          : "所有录音已通过安全检查，可随结构化复盘提交。",
      });
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
      this.setData({ submissionStatus: "error", submissionMessage: closureErrorMessage(error) });
    }
  },

  playVoiceSegment(event: WechatMiniprogram.BaseEvent) {
    const runtime = pageRuntimes.get(this);
    const segmentId = String(event.currentTarget.dataset.segmentId ?? "");
    const segment = this.data.voiceSegments.find((item) => item.segmentId === segmentId);
    if (!runtime?.voicePreview || !segment) return;
    runtime.voicePreview.play(
      segment.localPath,
      () => {
        this.setData({ voicePlayingSegmentId: "" });
      },
      (error) => {
        this.setData({
          voicePlayingSegmentId: "",
          submissionStatus: "error",
          submissionMessage: error.message,
        });
      },
    );
    this.setData({
      voicePlayingSegmentId: this.data.voicePlayingSegmentId === segmentId ? "" : segmentId,
    });
  },

  async removeVoiceSegment(event: WechatMiniprogram.BaseEvent) {
    const runtime = pageRuntimes.get(this);
    const segmentId = String(event.currentTarget.dataset.segmentId ?? "");
    const segment = this.data.voiceSegments.find((item) => item.segmentId === segmentId);
    if (!runtime || !segment || ["authorizing", "uploading", "scanning"].includes(segment.state))
      return;
    const confirm = await wx.showModal({
      title: "删除这段录音？",
      content: "删除后不会随本次申论提交，本地录音也会一并清理。",
      confirmText: "确认删除",
    });
    if (!confirm.confirm || !pageRuntimes.owns(this, runtime)) return;
    try {
      await deleteLocalVoice(segment.localPath);
      if (!pageRuntimes.owns(this, runtime)) return;
      const segments = this.data.voiceSegments.filter((item) => item.segmentId !== segmentId);
      persistVoiceSegments(runtime, segments);
      applyVoiceSegments(this, segments);
    } catch (error) {
      this.setData({ submissionStatus: "error", submissionMessage: closureErrorMessage(error) });
    }
  },

  async submitStage() {
    const runtime = pageRuntimes.get(this);
    if (runtime?.activeStage === "essay_drafting") {
      await this.submitDrafting();
      return;
    }
    if (runtime?.activeStage === "essay_rewrite") {
      await this.submitRewrite();
      return;
    }
    if (runtime?.activeStage === "essay_transfer") {
      await this.submitTransfer();
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
        if (this.data.voiceSegments.some((segment) => segment.state !== "available")) {
          throw new EssayDraftingError(
            "请先上传或删除未就绪录音；上传失败时作答和录音草稿都会保留。",
          );
        }
        const voiceArtifactVersionIds = this.data.voiceSegments.map((segment) => {
          if (!segment.artifactVersionId) {
            throw new EssayDraftingError("这段录音的上传结果不完整，请重新上传。");
          }
          return segment.artifactVersionId;
        });
        const command = buildDraftingCompletionCommand(
          this.data.workspace,
          this.data.drafting,
          Math.max(1, currentElapsedSeconds(runtime)),
          {
            planTaskId: runtime.activePlanTaskId,
            ...(runtime.basedOnExecutionVersionId
              ? { basedOnExecutionVersionId: runtime.basedOnExecutionVersionId }
              : {}),
            voiceArtifactVersionIds,
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
        discardDraftingAutosave(runtime);
        clearEssayDraftingDraft(runtime.activeUserId, runtime.activePlanTaskId);
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
      clearReflectionLearningMediaDraft(
        runtime.activeUserId,
        voiceDraftScope(runtime.activePlanTaskId),
      );
      clearEssayDraftingDraft(runtime.activeUserId, runtime.activePlanTaskId);
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
        submissionMessage: "答案和复盘已保存，正在启动 AI 评阅…",
        blockingReason: "本次申论任务已经完成；AI 评阅会在后台继续，不影响已保存的作答。",
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
        submissionMessage: "这版答案还没有对应的评阅结果，请重新加载后核对。",
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
          "评阅任务已排队，但没有拿到对应处理记录。请重新加载核对状态。",
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
      if (enterEssayAuthRequired(this, runtime, error)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        submissionStatus: "error",
        submissionMessage: apiError
          ? `评阅状态暂未同步：${apiError.message}。进度已保留，将继续查询。`
          : "评阅状态暂未同步，进度已保留，将继续查询。",
        submissionRequestId: apiError?.requestId ?? "",
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
      submissionMessage: "正在重新开始一次完整评阅…",
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
      if (enterEssayAuthRequired(this, runtime, error)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        submissionStatus: "error",
        submissionMessage: apiError
          ? `${apiError.message}。原评阅进度已保留，可以直接重试。`
          : "刚才的重评结果还不确定，原评阅进度已保留，可以直接重试。",
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
      if (enterEssayAuthRequired(this, runtime, error)) return;
      const apiError = error instanceof ApiClientError ? error : null;
      this.setData({
        submissionStatus: "error",
        submissionMessage: apiError
          ? `完整评阅没有加载成功：${apiError.message}`
          : "完整评阅没有加载成功。",
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
        submissionMessage: "正在保存复盘并完成今日任务…",
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
        blockingReason: "本阶段学习记录和今日任务已经保存，请返回今日页继续。",
        submissionMessage: "本次学习记录已保存，今日任务已完成。",
      });
    } catch (error) {
      if (!pageRuntimes.isCurrent(this, runtime, requestEpoch)) return;
      if (enterEssayAuthRequired(this, runtime, error)) return;
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
            ? "任务已经在其他设备或页面更新，本次没有写入不完整记录。请从今日页重新进入。"
            : apiError
              ? apiError.message
              : "答案、复盘和任务完成状态还没有完全确认，请直接重试。";
      this.setData({
        submissionStatus: "error",
        submissionMessage: message,
        draftLocked: runtime.pendingCompletion !== null,
        canSubmit: conflict ? false : this.data.canSubmit,
        blockingReason: conflict
          ? "当前任务已经失效，请从今日页重新进入；其他设备保存的内容不会被覆盖。"
          : this.data.blockingReason,
      });
    }
  },
});

function bindEssaySession(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  session: ActiveAuthSession,
): void {
  if (!runtime.activeStage) return;
  if (runtime.activeUserId && runtime.activeUserId !== session.userId) {
    flushDraftingAutosave(page, runtime);
  }
  pageRuntimes.beginRequest(runtime);
  stopTimer(runtime);
  stopReviewPoll(runtime);
  runtime.voiceRecorder?.dispose();
  runtime.voicePreview?.dispose();
  runtime.activeUserId = session.userId;
  runtime.activeAccessToken = session.accessToken;
  runtime.activeSessionGeneration = session.generation;
  runtime.pendingCompletion = null;
  runtime.pendingDraftingSubmission = null;
  runtime.closureDraft = null;
  runtime.rewriteReview = null;
  runtime.transferSourceReview = null;
  runtime.transferReview = null;
  runtime.activeReviewIntent = null;
  runtime.activeReviewJobId = "";

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
  if (runtime.pendingDraftingSubmission) {
    clearEssayDraftingDraft(session.userId, runtime.activePlanTaskId);
  }
  if (runtime.activeStage === "essay_rewrite" || runtime.activeStage === "essay_transfer") {
    const writerContext = runtime.writerContext;
    if (writerContext?.kind === "rewrite") {
      runtime.closureDraft = loadEssayClosureDraft(session.userId, {
        kind: "rewrite",
        taskVersionId: runtime.activeTaskVersionId,
        planTaskId: runtime.activePlanTaskId,
        writerContext,
      });
    } else if (writerContext?.kind === "transfer") {
      runtime.closureDraft = loadEssayClosureDraft(session.userId, {
        kind: "transfer",
        taskVersionId: runtime.activeTaskVersionId,
        planTaskId: runtime.activePlanTaskId,
        writerContext,
      });
    }
  }

  const client = new ApiClient(environment.apiBaseUrl, session.accessToken);
  runtime.api = new EssayIntelligenceApi(client);
  runtime.essayHandwritingApi = new EssayHandwritingApi(client);
  runtime.learningMediaApi = new LearningMediaApi(client);
  runtime.voiceUploadCoordinator = new ReflectionVoiceUploadCoordinator(runtime.learningMediaApi);
  runtime.voiceRecorder = new WechatVoiceRecorder();
  runtime.voicePreview = new WechatVoicePreview();
  page.setData({
    showLoginAction: false,
    voiceRecording: false,
    voicePlayingSegmentId: "",
  });
}

function enterEssayAuthRequired(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  error?: unknown,
): boolean {
  const apiError = error instanceof ApiClientError ? error : null;
  if (error !== undefined && apiError?.statusCode !== 401) return false;
  if (!pageRuntimes.owns(page, runtime)) return true;

  flushDraftingAutosave(page, runtime);
  pageRuntimes.beginRequest(runtime);
  runtime.pageVisible = false;
  stopTimer(runtime);
  stopReviewPoll(runtime);
  runtime.reviewPollInFlight = false;
  runtime.voiceRecorder?.stop();
  runtime.voicePreview?.stop();
  runtime.api = null;
  runtime.essayHandwritingApi = null;
  runtime.learningMediaApi = null;
  runtime.voiceUploadCoordinator = null;
  page.setData({
    status: "unavailable",
    errorTitle: apiError ? "登录状态已过期" : "登录后才能继续申论任务",
    errorMessage: apiError
      ? `本页进度已保留，请重新登录后继续。`
      : "学习记录必须绑定你的账号，请重新登录后继续。",
    showLoginAction: true,
    submissionStatus: "error",
    submissionMessage: "本页进度已保留，重新登录后会从服务器和本机记录恢复。",
    draftLocked: true,
    canSubmit: false,
  });
  return true;
}

function essayTaskReturnUrl(
  options: Record<string, string | undefined>,
  stage: EssayTaskStage | null,
): string {
  if (!stage) return "";
  const values: Record<string, string | undefined> = {
    stage,
    taskVersionId: options.taskVersionId,
    planTaskId: options.planTaskId,
    basedOnExecutionVersionId: options.basedOnExecutionVersionId,
    attemptId: options.attemptId,
    baseReviewVersionId: options.baseReviewVersionId,
    baseAnswerVersionId: options.baseAnswerVersionId,
    findingDecisionArtifactVersionId: options.findingDecisionArtifactVersionId,
    basedOnAnswerVersionId: options.basedOnAnswerVersionId,
    basedOnRewriteVersionId: options.basedOnRewriteVersionId,
    materialBundleVersionId: options.materialBundleVersionId,
    sourceRewriteVersionId: options.sourceRewriteVersionId,
    sourceFindingDecisionArtifactVersionId: options.sourceFindingDecisionArtifactVersionId,
    basedOnTransferVerificationVersionId: options.basedOnTransferVerificationVersionId,
  };
  const query = Object.entries(values)
    .flatMap(([key, value]) => {
      const normalized = value?.trim() ?? "";
      return normalized ? [`${encodeURIComponent(key)}=${encodeURIComponent(normalized)}`] : [];
    })
    .join("&");
  return query ? `${routes.essayTask}?${query}` : "";
}

function persistRewriteDraft(
  page: EssayTaskPageInstance,
  draft: EssayRewriteDraft,
  preserveIdempotencyKey: boolean,
): void {
  const runtime = pageRuntimes.get(page);
  if (
    !runtime ||
    runtime.activeStage !== "essay_rewrite" ||
    runtime.writerContext?.kind !== "rewrite"
  ) {
    return;
  }
  const existing = runtime.closureDraft?.kind === "rewrite" ? runtime.closureDraft : null;
  runtime.closureDraft = {
    kind: "rewrite",
    taskVersionId: runtime.activeTaskVersionId,
    planTaskId: runtime.activePlanTaskId,
    writerContext: runtime.writerContext,
    draft,
    ...(preserveIdempotencyKey && existing?.idempotencyKey
      ? { idempotencyKey: existing.idempotencyKey }
      : {}),
  };
  storeEssayClosureDraft(runtime.activeUserId, runtime.closureDraft);
}

function persistTransferDraft(page: EssayTaskPageInstance, draft: EssayTransferDraft): void {
  const runtime = pageRuntimes.get(page);
  if (
    !runtime ||
    runtime.activeStage !== "essay_transfer" ||
    runtime.writerContext?.kind !== "transfer"
  ) {
    return;
  }
  runtime.closureDraft = {
    kind: "transfer",
    taskVersionId: runtime.activeTaskVersionId,
    planTaskId: runtime.activePlanTaskId,
    writerContext: runtime.writerContext,
    draft,
  };
  storeEssayClosureDraft(runtime.activeUserId, runtime.closureDraft);
}

function applyRewriteResult(page: EssayTaskPageInstance, result: EssayRewriteResultV2): void {
  applyRewriteCompletion(page, {
    status: result.rewrite.status,
    versionId: result.rewrite.rewriteVersionId,
    masteryLabel: masterySummary(result.masteryVersions),
  });
}

function applyRewriteCompletion(
  page: EssayTaskPageInstance,
  result: {
    status: "passed" | "needs_revision";
    versionId: string;
    masteryLabel: string;
  },
): void {
  const passed = result.status === "passed";
  page.setData({
    closureResult: {
      statusLabel: passed ? "改写通过" : "仍需修正",
      statusTone: passed ? "success" : "warning",
      versionId: result.versionId,
      masteryLabel: result.masteryLabel,
      planningMessage: "本次改写和掌握变化已保存，后续计划会据此安排迁移练习。",
    },
    hasSavedEvidence: true,
    submissionStatus: "saved",
    submissionMessage: passed
      ? "新答案和逐条修正记录已保存，下一步可以做迁移练习。"
      : "新答案已保存，未解决问题仍保持待改写。",
    draftLocked: true,
    canSubmit: false,
  });
}

function applyTransferCompletion(
  page: EssayTaskPageInstance,
  result: {
    status: "verified" | "not_verified" | "inconclusive";
    versionId: string;
    masteryLabel: string;
  },
): void {
  const tone: ClosureResultView["statusTone"] =
    result.status === "verified"
      ? "success"
      : result.status === "not_verified"
        ? "danger"
        : "warning";
  const label = {
    verified: "迁移已验证",
    not_verified: "迁移未验证",
    inconclusive: "迁移证据不充分",
  }[result.status];
  page.setData({
    closureResult: {
      statusLabel: label,
      statusTone: tone,
      versionId: result.versionId,
      masteryLabel: result.masteryLabel,
      planningMessage: "本次迁移结果和掌握变化已保存，后续计划会据此继续复测。",
    },
    transferDraft: { ...page.data.transferDraft, phase: "completed" },
    hasSavedEvidence: true,
    submissionStatus: "saved",
    submissionMessage: `迁移验证已保存：${label}。`,
    draftLocked: true,
    canSubmit: false,
  });
}

function scheduleTransferPoll(
  page: EssayTaskPageInstance,
  runtime: EssayTaskPageRuntime,
  delayMs: number,
): void {
  stopReviewPoll(runtime);
  if (!runtime.pageVisible || runtime.disposed) return;
  runtime.reviewPollHandle = setTimeout(() => {
    runtime.reviewPollHandle = undefined;
    if (pageRuntimes.owns(page, runtime)) void page.pollTransferReview();
  }, delayMs);
}

function closureErrorMessage(error: unknown): string {
  if (error instanceof EssayClosureError || error instanceof EssayDraftingError) {
    return error.message;
  }
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return error instanceof Error ? error.message : "申论内容没有完整保存，请重试。";
}

function handwritingErrorMessage(error: unknown): string {
  if (error instanceof EssayHandwritingError) return error.message;
  if (error instanceof ApiClientError) {
    return error.message;
  }
  return error instanceof Error ? error.message : "手写识别没有形成可确认的文字。";
}

function essayWorkbenchLinks(
  workspace: EssayTaskWorkspace,
  planTaskId: string,
): Array<{ label: string; url: string }> {
  const task = encodeURIComponent(workspace.task.taskVersionId);
  const plan = encodeURIComponent(planTaskId);
  const suffix = `taskVersionId=${task}&planTaskId=${plan}`;
  const links = [
    { label: "材料地图", url: `${routes.essayMaterial}?${suffix}` },
    { label: "要点与正文", url: `${routes.essayCompose}?${suffix}` },
  ];
  const reviewVersionId = workspace.progression.reviewVersionId;
  if (reviewVersionId) {
    const review = encodeURIComponent(reviewVersionId);
    links.push(
      { label: "AI 评阅", url: `${routes.essayReview}?${suffix}&reviewVersionId=${review}` },
      { label: "评阅证据", url: `${routes.essayEvidence}?${suffix}&reviewVersionId=${review}` },
    );
  }
  const rewriteVersionId = workspace.progression.rewriteVersionId;
  if (rewriteVersionId) {
    links.push({
      label: "改写对照",
      url: `${routes.essayRewrite}?${suffix}&rewriteVersionId=${encodeURIComponent(rewriteVersionId)}`,
    });
  }
  return links;
}

function isTerminalClosureConflict(error: unknown): boolean {
  return error instanceof ApiClientError && error.statusCode === 409;
}

function voiceDraftScope(planTaskId: string): string {
  return `essay-drafting:${planTaskId}`;
}

function persistVoiceSegments(
  runtime: EssayTaskPageRuntime,
  segments: ReflectionVoiceSegment[],
): void {
  const scope = voiceDraftScope(runtime.activePlanTaskId);
  const current = loadReflectionLearningMediaDraft(runtime.activeUserId, scope);
  storeReflectionLearningMediaDraft(runtime.activeUserId, scope, {
    ...current,
    voiceSegments: segments,
  });
}

function applyVoiceSegments(page: EssayTaskPageInstance, segments: ReflectionVoiceSegment[]): void {
  page.setData({
    voiceSegments: segments,
    voiceSegmentViews: presentReflectionVoiceSegments(segments),
    voiceSummary: presentReflectionVoiceSummary(segments),
  });
}

function datasetIndex(event: WechatMiniprogram.BaseEvent): number {
  return Number(event.currentTarget.dataset.index ?? -1);
}

const draftingAutosaveDelayMs = 450;

function markDraftChanged(page: EssayTaskPageInstance): void {
  const runtime = pageRuntimes.get(page);
  if (
    !runtime ||
    runtime.activeStage !== "essay_drafting" ||
    runtime.pendingDraftingSubmission ||
    page.data.draftLocked ||
    !page.data.draftAvailable
  ) {
    return;
  }
  if (runtime.draftingAutosaveHandle !== undefined) {
    clearTimeout(runtime.draftingAutosaveHandle);
  }
  runtime.draftingAutosaveHandle = setTimeout(() => {
    runtime.draftingAutosaveHandle = undefined;
    persistDraftingAutosave(page, runtime);
  }, draftingAutosaveDelayMs);
}

function flushDraftingAutosave(page: EssayTaskPageInstance, runtime: EssayTaskPageRuntime): void {
  discardDraftingAutosave(runtime);
  persistDraftingAutosave(page, runtime);
}

function discardDraftingAutosave(runtime: EssayTaskPageRuntime): void {
  if (runtime.draftingAutosaveHandle !== undefined) {
    clearTimeout(runtime.draftingAutosaveHandle);
  }
  runtime.draftingAutosaveHandle = undefined;
}

function persistDraftingAutosave(page: EssayTaskPageInstance, runtime: EssayTaskPageRuntime): void {
  if (
    !pageRuntimes.owns(page, runtime) ||
    runtime.activeStage !== "essay_drafting" ||
    runtime.pendingDraftingSubmission ||
    !runtime.activeUserId ||
    !runtime.activeTaskVersionId ||
    !runtime.activePlanTaskId ||
    page.data.draftLocked ||
    !page.data.draftAvailable
  ) {
    return;
  }
  try {
    storeEssayDraftingDraft(
      runtime.activeUserId,
      {
        taskVersionId: runtime.activeTaskVersionId,
        planTaskId: runtime.activePlanTaskId,
      },
      page.data.drafting,
    );
  } catch {
    page.setData({
      submissionStatus: "error",
      submissionMessage:
        "这次输入暂时没有保存到本机。请先保持页面打开；若再次出现，请检查微信可用存储空间后重试。",
    });
  }
}

function scrollTo(anchorId: string): void {
  void wx.pageScrollTo({ selector: `#${anchorId}`, duration: 260 });
}

function appendDraftText(current: string, addition: string): string {
  const normalizedCurrent = current.replace(/\s+$/u, "");
  return normalizedCurrent ? `${normalizedCurrent}\n${addition}` : addition;
}

function startTimer(page: EssayTaskPageInstance, runtime: EssayTaskPageRuntime): void {
  stopTimer(runtime);
  runtime.activeSince = Date.now();
  const update = () => {
    if (!pageRuntimes.owns(page, runtime)) return;
    const elapsedSeconds = currentElapsedSeconds(runtime);
    const hours = Math.floor(elapsedSeconds / 3600);
    const minutes = Math.floor((elapsedSeconds % 3600) / 60);
    const seconds = elapsedSeconds % 60;
    page.setData({
      elapsedLabel: `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`,
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
    return "正在保存答案和复盘，并完成今天的申论任务…";
  }
  if (!pending.ensure?.jobId) return "答案已保存，正在确认完整评阅…";
  return "评阅已开始，正在等待完整结果…";
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
  if (enterEssayAuthRequired(page, runtime, error)) return;
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
          ? "任务已经在其他设备或页面更新，本次没有写入不完整记录。请从今日页重新进入。"
          : "作答和计划已经保存，但评阅依据发生变化。请返回今日页重新进入。"
        : apiError
          ? `${apiError.message}。${completionPending ? "刚才的提交内容已保留" : "评阅任务已保存在账户中"}，可以直接重试。`
          : `请求没有获得完整响应。${completionPending ? "刚才的提交内容已保留" : "评阅任务已保存在账户中"}，可以直接重试。`;
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
      findings: view.findings.map((item) => {
        const decision = drafts.review.decisions.find(
          (draft) => draft.findingId === item.findingId,
        );
        return {
          ...item,
          decision: decision?.decision ?? "",
          decisionIndex: decision?.decisionIndex ?? 0,
          learnerReason: decision?.learnerReason ?? "",
          followUpQuestion: decision?.followUpQuestion ?? "",
          priority: drafts.review.priorityFindingIds.includes(item.findingId),
          priorityDisabled: decision?.decision !== "accepted",
        };
      }),
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
