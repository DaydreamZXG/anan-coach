import { ApiClient, ApiClientError } from "../../api/client";
import { LearningMediaApi } from "../../api/learning-media";
import { XfyunApi } from "../../api/xfyun";
import { XingceLearningApi, type XingceTaskSession } from "../../api/xingce-learning";
import { routes } from "../../app/navigation/routes";
import { navigateBackOr } from "../../app/navigation/safe-back";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import {
  clearReflectionLearningMediaDraft,
  loadReflectionLearningMediaDraft,
  storeReflectionLearningMediaDraft,
} from "../../features/learning-media/draft-store";
import {
  maximumReflectionVoiceDurationMilliseconds,
  maximumReflectionVoiceSegments,
  maximumReflectionVoiceTotalBytes,
  validateVoiceCollection,
  type ReflectionLearningMediaDraft,
  type ReflectionVoiceSegment,
} from "../../features/learning-media/contracts";
import {
  deleteLocalVoice,
  ensureRecordPermission,
  persistSynthesizedSpeech,
  persistRecordedVoice,
  readVoiceFileBase64,
  requestRecordSetting,
  WechatVoicePreview,
  WechatVoiceRecorder,
  type RecordedVoiceFile,
} from "../../features/learning-media/platform";
import {
  presentReflectionVoiceSegments,
  presentReflectionVoiceSummary,
  type ReflectionVoiceSegmentView,
  type ReflectionVoiceSummaryView,
} from "../../features/learning-media/presentation";
import {
  LearningMediaOperationCancelled,
  ReflectionVoiceUploadCoordinator,
} from "../../features/learning-media/upload-controller";
import {
  isXingceReflectionTag,
  isXingceReplacementCauseCode,
  methodInterpretationEligibleQuestionIds,
  xingceReflectionTagLabels,
  xingceReflectionTags,
  xingceReplacementCauseCodeLabels,
  xingceReplacementCauseCodes,
  type XingceReflectionTag,
} from "../../features/xingce/real_attempt/contracts";
import {
  presentXingceCauseCandidate,
  presentXingceImmediateVerification,
  presentXingceReflectionClosure,
  presentXingceResult,
  xingceReflectionReviewItems,
  type XingceReflectionClosureView,
  type XingceCauseCandidateView,
  type XingceResultItemView,
  type XingceResultView,
} from "../../features/xingce/real_attempt/presentation";
import {
  clearPendingXingceCauseDecision,
  clearPendingXingceCauseFollowup,
  clearPendingXingceReflection,
  loadPendingXingceCauseDecision,
  loadPendingXingceCauseFollowup,
  loadPendingXingceReflection,
  storePendingXingceCauseDecision,
  storePendingXingceCauseFollowup,
  storePendingXingceReflection,
} from "../../features/xingce/real_attempt/pending-store";
import { publishLatestTodayDashboard } from "../../features/planning/runtime-state";
import { createIdempotencyKey } from "../../platform/command-id";
import type {
  ContentAnchor,
  ContentDisplayPreferences,
  RichContentBlock,
} from "../../task-runtime/content-renderer/model";
import {
  loadTaskDisplayPreferences,
  storeTaskDisplayPreferences,
} from "../../task-runtime/display-preferences";
import {
  createMaterialReaderState,
  reduceMaterialReader,
  type MaterialReaderState,
} from "../../task-runtime/material-reader/state";
import {
  resolveSwipeNav,
  swipeSampleFromTouchEnd,
  swipeSampleFromTouchMove,
  swipeSampleFromTouchStart,
  type SwipeNavSample,
} from "../../task-runtime/swipe-nav";

type ReflectionStatus = "loading" | "ready" | "error" | "unavailable";
type ReflectionPhase =
  | "summary"
  | "reflection"
  | "cause_processing"
  | "cause_followup"
  | "cause_decision"
  | "cause_insufficient"
  | "cause_terminated"
  | "completed";

interface ReflectionPageData {
  status: ReflectionStatus;
  phase: ReflectionPhase;
  errorTitle: string;
  errorMessage: string;
  showLoginAction: boolean;
  result: XingceResultView | null;
  summaryBasisOpen: boolean;
  /** Figma 75:2 · 原题轨迹回放。 */
  replayOpen: boolean;
  replayPlaying: boolean;
  replayMs: number;
  replayDurationMs: number;
  replayProgressPercent: number;
  replayTimeLabel: string;
  replayCurrentLabel: string;
  replaySummaryLabel: string;
  replayCurrentEvent: string;
  replayMarkers: Array<{ percent: number; tone: "default" | "warning" }>;
  /** Figma 78:2 · 岸岸动作分析。 */
  analysisOpen: boolean;
  actionAnalysis: {
    reliabilityLabel: string;
    basisLabel: string;
    summary: string;
    rows: Array<{ timeLabel: string; tone: "default" | "warning"; text: string }>;
  } | null;
  rawText: string;
  strategyNote: string;
  doubtNote: string;
  tagOptions: Array<{ code: XingceReflectionTag; label: string; selected: boolean }>;
  causeCandidate: XingceCauseCandidateView | null;
  followupAnswer: string;
  replacementCause: string;
  /** 改写错因的可选结构化 8 码单选 chip（可不选，单选可再点取消）。 */
  replacementCodeOptions: Array<{ code: string; label: string; selected: boolean }>;
  interactionLocked: boolean;
  pendingMessage: string;
  hasPendingWrite: boolean;
  recordPermission: "unknown" | "granted" | "denied";
  isRecording: boolean;
  voiceBusy: boolean;
  playingVoiceId: string;
  voiceMessage: string;
  voiceSegments: ReflectionVoiceSegmentView[];
  voiceSummary: ReflectionVoiceSummaryView;
  speechBusy: boolean;
  speechPlaying: boolean;
  interpretationOptions: Array<{
    questionId: string;
    positionLabel: string;
    answerLabel: string;
    selected: boolean;
  }>;
  reviewItems: XingceResultItemView[];
  activeReviewItem: XingceResultItemView | null;
  currentReviewIndex: number;
  materialReaderState: MaterialReaderState;
  displayPreferences: ContentDisplayPreferences;
  emptyRichContentBlocks: RichContentBlock[];
  emptyContentAnchors: ContentAnchor[];
  moreEvidenceOpen: boolean;
  completionClosure: XingceReflectionClosureView;
  hasImmediateVerification: boolean;
  verificationNavigationLocked: boolean;
}

interface ReflectionPageCustom {
  loadReview(): Promise<void>;
  retry(): void;
  goLogin(): void;
  exit(): Promise<void>;
  handleBack(): void;
  startReflection(): void;
  showSummary(): void;
  toggleSummaryBasis(): void;
  askAnanAboutGroup(): void;
  updateMaterialReader(event: WechatMiniprogram.CustomEvent<{ state: MaterialReaderState }>): void;
  inputRawText(event: WechatMiniprogram.Input): void;
  inputStrategyNote(event: WechatMiniprogram.Input): void;
  inputDoubtNote(event: WechatMiniprogram.Input): void;
  toggleTag(event: WechatMiniprogram.BaseEvent): void;
  toggleMethodInterpretation(event: WechatMiniprogram.BaseEvent): void;
  startVoiceRecording(): Promise<void>;
  stopVoiceRecording(): void;
  openRecordPermission(): Promise<void>;
  uploadVoiceSegments(): Promise<void>;
  playVoice(event: WechatMiniprogram.BaseEvent): void;
  transcribeVoice(event: WechatMiniprogram.BaseEvent): Promise<void>;
  readRawText(): Promise<void>;
  deleteVoice(event: WechatMiniprogram.BaseEvent): Promise<void>;
  rerecordVoice(event: WechatMiniprogram.BaseEvent): Promise<void>;
  submitReflection(): Promise<void>;
  refreshCauseCandidate(): Promise<void>;
  inputFollowupAnswer(event: WechatMiniprogram.Input): void;
  submitCauseFollowup(): Promise<void>;
  inputReplacementCause(event: WechatMiniprogram.Input): void;
  selectReplacementCode(event: WechatMiniprogram.BaseEvent): void;
  confirmCause(): Promise<void>;
  rejectCause(): Promise<void>;
  replaceCause(): Promise<void>;
  deferCause(): Promise<void>;
  returnToday(): void;
  startImmediateVerification(): Promise<void>;
  selectReviewItem(event: WechatMiniprogram.BaseEvent): void;
  previousReviewItem(): void;
  onReviewTouchStart(event: WechatMiniprogram.TouchEvent): void;
  onReviewTouchMove(event: WechatMiniprogram.TouchEvent): void;
  onReviewTouchEnd(event: WechatMiniprogram.TouchEvent): void;
  openReviewMaterial(): void;
  confirmCauseAndNext(): void;
  openReplay(): void;
  closeReplay(): void;
  toggleReplayPlayback(): void;
  openActionAnalysis(): void;
  closeActionAnalysis(): void;
  toggleMoreEvidence(): void;
  noop(): void;
}

interface ReflectionRuntime {
  api: XingceLearningApi;
  mediaApi: LearningMediaApi;
  xfyunApi: XfyunApi;
  mediaUploader: ReflectionVoiceUploadCoordinator;
  ownerUserId: string;
  sessionId: string;
  reviewId: string;
  session: XingceTaskSession | null;
  selectedTags: Set<XingceReflectionTag>;
  selectedInterpretationQuestionIds: Set<string>;
  disposed: boolean;
  requestEpoch: number;
  submitting: boolean;
  mediaDraft: ReflectionLearningMediaDraft;
  mediaEpoch: number;
  mediaBusy: boolean;
  recorder: WechatVoiceRecorder | null;
  preview: WechatVoicePreview | null;
  speechPreview: WechatVoicePreview | null;
  replacingSegmentId: string | null;
  causePollTimer: ReturnType<typeof setTimeout> | null;
  causePollCount: number;
  reviewFormEntered: boolean;
  verificationNavigationStarted: boolean;
  swipeStart: SwipeNavSample | null;
  swipeLatest: SwipeNavSample | null;
}

type ReflectionPageInstance = WechatMiniprogram.Page.Instance<
  ReflectionPageData,
  ReflectionPageCustom
>;

const runtimes = new WeakMap<ReflectionPageInstance, ReflectionRuntime>();

Page<ReflectionPageData, ReflectionPageCustom>({
  data: {
    status: "loading",
    phase: "summary",
    errorTitle: "",
    errorMessage: "",
    showLoginAction: false,
    result: null,
    summaryBasisOpen: false,
    replayOpen: false,
    replayPlaying: false,
    replayMs: 0,
    replayDurationMs: 0,
    replayProgressPercent: 0,
    replayTimeLabel: "00:00 / 00:00",
    replayCurrentLabel: "00:00",
    replaySummaryLabel: "",
    replayCurrentEvent: "",
    replayMarkers: [],
    analysisOpen: false,
    actionAnalysis: null,
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    tagOptions: tagOptions(new Set()),
    causeCandidate: null,
    followupAnswer: "",
    replacementCause: "",
    replacementCodeOptions: replacementCodeOptions(""),
    interactionLocked: false,
    pendingMessage: "",
    hasPendingWrite: false,
    recordPermission: "unknown",
    isRecording: false,
    voiceBusy: false,
    playingVoiceId: "",
    voiceMessage: "",
    voiceSegments: [],
    voiceSummary: presentReflectionVoiceSummary([]),
    speechBusy: false,
    speechPlaying: false,
    interpretationOptions: [],
    reviewItems: [],
    activeReviewItem: null,
    currentReviewIndex: 0,
    materialReaderState: createMaterialReaderState(),
    displayPreferences: { fontScale: 1, colorMode: "standard" },
    emptyRichContentBlocks: [],
    emptyContentAnchors: [],
    moreEvidenceOpen: false,
    completionClosure: {
      acceptedCorrection: null,
      statusTitle: "这次没有形成确认纠偏",
      statusDescription: "当前没有完整的结算记录，不会把候选判断当成已确认结论。",
    },
    hasImmediateVerification: false,
    verificationNavigationLocked: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const displayPreferences = loadTaskDisplayPreferences();
    this.setData({
      displayPreferences,
      materialReaderState: createMaterialReaderState(undefined, displayPreferences),
    });
    const sessionId = options.sessionId?.trim() ?? "";
    const reviewId = options.reviewId?.trim() ?? "";
    if (!sessionId || !reviewId) {
      this.setData({
        status: "error",
        errorTitle: "复盘地址不完整",
        errorMessage: "缺少作答会话或复盘记录，不能单独提交复盘。",
      });
      return;
    }
    const auth = currentAuthSession();
    if (!auth) {
      this.setData({
        status: "unavailable",
        errorTitle: "登录后才能继续复盘",
        errorMessage: "复盘是个人学习档案，当前没有可用登录会话。",
        showLoginAction: true,
      });
      return;
    }
    const client = new ApiClient(environment.apiBaseUrl, auth.accessToken);
    const mediaApi = new LearningMediaApi(client);
    const mediaDraft = loadReflectionLearningMediaDraft(auth.userId, reviewId);
    const runtime: ReflectionRuntime = {
      api: new XingceLearningApi(client),
      mediaApi,
      xfyunApi: new XfyunApi(client),
      mediaUploader: new ReflectionVoiceUploadCoordinator(mediaApi),
      ownerUserId: auth.userId,
      sessionId,
      reviewId,
      session: null,
      selectedTags: new Set(),
      selectedInterpretationQuestionIds: new Set(
        mediaDraft.methodCorrectCalculationErrorQuestionIds,
      ),
      disposed: false,
      requestEpoch: 0,
      submitting: false,
      mediaDraft,
      mediaEpoch: 0,
      mediaBusy: false,
      recorder: null,
      preview: null,
      speechPreview: null,
      replacingSegmentId: null,
      causePollTimer: null,
      causePollCount: 0,
      reviewFormEntered: hasReflectionDraft(mediaDraft),
      verificationNavigationStarted: false,
      swipeStart: null,
      swipeLatest: null,
    };
    runtimes.set(this, runtime);
    syncVoiceView(this, runtime);
    void this.loadReview();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    runtime.verificationNavigationStarted = false;
    this.setData({ verificationNavigationLocked: false });
    if (!currentAccountOwns(runtime)) {
      invalidateForAccountChange(this, runtime);
      return;
    }
    if (!runtime.session) return;
    void this.loadReview();
  },

  onUnload() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    runtime.disposed = true;
    runtime.requestEpoch += 1;
    runtime.mediaEpoch += 1;
    clearCausePoll(runtime);
    runtime.recorder?.dispose();
    runtime.preview?.dispose();
    runtime.speechPreview?.dispose();
    runtimes.delete(this);
  },

  async loadReview() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    const epoch = ++runtime.requestEpoch;
    this.setData({ status: "loading", errorTitle: "", errorMessage: "" });
    try {
      const response = await runtime.api.getSession(runtime.sessionId);
      if (!isCurrent(this, runtime, epoch)) return;
      if (!currentAccountOwns(runtime)) {
        invalidateForAccountChange(this, runtime);
        return;
      }
      applySession(this, runtime, response.data);
    } catch (error) {
      if (!isCurrent(this, runtime, epoch)) return;
      handleError(this, error, "真实复盘状态尚未载入");
    }
  },

  retry() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    if (loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId)) {
      void this.submitReflection();
      return;
    }
    if (loadPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId)) {
      void submitCauseFollowup(this, runtime);
      return;
    }
    if (loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId)) {
      void decideCause(this, runtime);
      return;
    }
    void this.loadReview();
  },

  goLogin() {
    void wx.redirectTo({ url: routes.login });
  },

  async exit() {
    if (this.data.phase !== "completed") {
      const result = await wx.showModal({
        title: "本次复盘还没完成",
        content: "退出后，这项任务仍会保留在待复盘列表中，可以稍后继续。",
        confirmText: "仍要退出",
      });
      if (!result.confirm) return;
    }
    navigateBackOr(routes.today);
  },

  handleBack() {
    if (this.data.phase === "reflection") {
      this.showSummary();
      return;
    }
    void this.exit();
  },

  startReflection() {
    const runtime = runtimes.get(this);
    if (
      !runtime?.session?.review ||
      runtime.session.review.state !== "reflection_required" ||
      this.data.status !== "ready"
    ) {
      return;
    }
    runtime.reviewFormEntered = true;
    this.setData({ phase: "reflection" });
  },

  showSummary() {
    const runtime = runtimes.get(this);
    if (
      !runtime?.session?.review ||
      runtime.session.review.state !== "reflection_required" ||
      this.data.status !== "ready"
    ) {
      return;
    }
    runtime.reviewFormEntered = false;
    this.setData({ phase: "summary" });
  },

  toggleSummaryBasis() {
    this.setData({ summaryBasisOpen: !this.data.summaryBasisOpen });
  },

  askAnanAboutGroup() {
    const runtime = runtimes.get(this);
    const auth = currentAuthSession();
    const result = this.data.result;
    if (
      !auth ||
      !runtime?.session ||
      runtime.session.state === "answering" ||
      !result ||
      result.availability !== "factored"
    ) {
      return;
    }
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "行测题组结果",
      title: `${runtime.session.bundle.questions[0]?.moduleName ?? "行测"} · ${String(result.totalCount)} 题`,
      summary: `${result.headline}；${result.summary}；总用时 ${result.metrics.find((item) => item.code === "elapsed")?.value ?? "未记录"}。`,
      suggestedQuestion:
        "结合这组题的正确性、用时和信心记录，告诉我最值得先改的一个问题，以及下一次怎么练。",
      routeId: "pages/reflection/index",
      workspace: "training",
      objectRefs: runtime.session.bundle.questions.map((question) => ({
        type: "question" as const,
        id: question.questionId,
        version: question.contentVersionId,
      })),
      taskMode: runtime.session.mode === "verification" ? "retest" : "practice",
      taskStage: "submitted",
    });
    void wx.switchTab({ url: routes.anan });
  },

  updateMaterialReader(event) {
    const next = event.detail.state;
    const preferencesChanged =
      next.preferences.fontScale !== this.data.displayPreferences.fontScale ||
      next.preferences.colorMode !== this.data.displayPreferences.colorMode;
    this.setData({
      materialReaderState: next,
      displayPreferences: next.preferences,
    });
    if (preferencesChanged) storeTaskDisplayPreferences(next.preferences);
  },

  inputRawText(event) {
    const runtime = runtimes.get(this);
    if (!runtime || this.data.interactionLocked) return;
    runtime.mediaDraft.rawText = event.detail.value;
    persistDraft(runtime);
    this.setData({ rawText: event.detail.value });
  },

  selectReviewItem(event) {
    const index = Number(event.currentTarget.dataset.index ?? -1);
    selectReviewIndex(this, index);
  },

  previousReviewItem() {
    selectReviewIndex(this, this.data.currentReviewIndex - 1);
  },

  onReviewTouchStart(event) {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed || reviewSwipeBlocked(this)) return;
    const sample = swipeSampleFromTouchStart(event);
    runtime.swipeStart = sample;
    runtime.swipeLatest = sample;
  },

  onReviewTouchMove(event) {
    const runtime = runtimes.get(this);
    if (!runtime?.swipeStart) return;
    if (event.touches.length > 1) {
      runtime.swipeStart = null;
      runtime.swipeLatest = null;
      return;
    }
    runtime.swipeLatest = swipeSampleFromTouchMove(event) ?? runtime.swipeLatest;
  },

  onReviewTouchEnd(event) {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    const start = runtime.swipeStart;
    const end =
      event.type === "touchcancel"
        ? runtime.swipeLatest
        : (swipeSampleFromTouchEnd(event) ?? runtime.swipeLatest);
    runtime.swipeStart = null;
    runtime.swipeLatest = null;
    if (!start || reviewSwipeBlocked(this)) return;
    const direction = resolveSwipeNav(start, end);
    if (!direction) return;
    const nextIndex = this.data.currentReviewIndex + (direction === "next" ? 1 : -1);
    if (nextIndex < 0 || nextIndex >= this.data.reviewItems.length) {
      void wx.showToast({
        title: direction === "next" ? "已经是最后一道关键题" : "已经是第一道关键题",
        icon: "none",
        duration: 1200,
      });
      return;
    }
    selectReviewIndex(this, nextIndex);
  },

  // Figma 14:2：收起的共享材料入口，点开进入沉浸阅读（恢复上次位置）。
  openReviewMaterial() {
    if (!this.data.activeReviewItem?.material) return;
    this.setData({
      materialReaderState: reduceMaterialReader(this.data.materialReaderState, {
        type: "OPEN_FULLSCREEN",
      }),
    });
  },

  // Figma 75:2：回放本题真实笔迹与动作事实（录制数据的播放，不是模拟流程）。
  openReplay() {
    const item = this.data.activeReviewItem;
    if (!item?.trajectoryAvailable) return;
    const durationMs = Math.max(
      item.elapsedSeconds * 1000,
      ...item.actionEvents.map((entry) => entry.atMs),
      ...item.annotations.map((stroke) => stroke.atMs),
      // 契约增量 v1：擦除墓碑也在时间线上，回放要走到擦除时刻。
      ...item.annotations.map((stroke) => stroke.erasedAtMs ?? 0),
      1000,
    );
    const eraseCount = item.actionEvents.filter(
      (entry) => entry.type === "annotation_erased",
    ).length;
    const changeCount = item.actionEvents.filter((entry) => entry.type === "option_changed").length;
    const summaryParts = [
      eraseCount > 0 ? `${String(eraseCount)} 次擦除` : "",
      changeCount > 0 ? `${String(changeCount)} 次回改` : "",
    ].filter(Boolean);
    this.setData({
      replayOpen: true,
      analysisOpen: false,
      replayPlaying: false,
      replayMs: 0,
      replayDurationMs: durationMs,
      replayProgressPercent: 0,
      replayTimeLabel: `00:00 / ${formatReplayTime(durationMs)}`,
      replayCurrentLabel: "00:00",
      replaySummaryLabel: summaryParts.join(" · ") || `${String(item.actionEvents.length)} 个动作`,
      replayCurrentEvent: "",
      replayMarkers: item.actionEvents.map((entry) => ({
        percent: Math.min(100, Math.round((entry.atMs / durationMs) * 100)),
        tone:
          entry.type === "option_changed" || entry.type === "annotation_erased"
            ? ("warning" as const)
            : ("default" as const),
      })),
    });
  },

  closeReplay() {
    stopReplayTimer(this);
    this.setData({ replayOpen: false, replayPlaying: false });
  },

  toggleReplayPlayback() {
    if (this.data.replayPlaying) {
      stopReplayTimer(this);
      this.setData({ replayPlaying: false });
      return;
    }
    const restart = this.data.replayMs >= this.data.replayDurationMs;
    this.setData({ replayPlaying: true, ...(restart ? { replayMs: 0 } : {}) });
    startReplayTimer(this);
  },

  // Figma 78:2：从真实动作事实推导的可见轨迹描述，不判断“粗心”或“不会”。
  openActionAnalysis() {
    const item = this.data.activeReviewItem;
    if (!item?.trajectoryAvailable) return;
    this.setData({
      analysisOpen: true,
      replayOpen: false,
      actionAnalysis: presentActionAnalysis(item),
    });
  },

  closeActionAnalysis() {
    this.setData({ analysisOpen: false });
  },

  // Figma 14:2 底部固定 CTA：先逐题看完，最后一题保存原话进入错因确认。
  confirmCauseAndNext() {
    const nextIndex = this.data.currentReviewIndex + 1;
    if (nextIndex < this.data.reviewItems.length) {
      selectReviewIndex(this, nextIndex);
      return;
    }
    void this.submitReflection();
  },

  toggleMoreEvidence() {
    this.setData({ moreEvidenceOpen: !this.data.moreEvidenceOpen });
  },

  inputStrategyNote(event) {
    const runtime = runtimes.get(this);
    if (!runtime || this.data.interactionLocked) return;
    runtime.mediaDraft.strategyNote = event.detail.value;
    persistDraft(runtime);
    this.setData({ strategyNote: event.detail.value });
  },

  inputDoubtNote(event) {
    const runtime = runtimes.get(this);
    if (!runtime || this.data.interactionLocked) return;
    runtime.mediaDraft.doubtNote = event.detail.value;
    persistDraft(runtime);
    this.setData({ doubtNote: event.detail.value });
  },

  toggleTag(event) {
    const runtime = runtimes.get(this);
    const code = String(event.currentTarget.dataset.code ?? "");
    if (!runtime || this.data.interactionLocked || !isXingceReflectionTag(code)) return;
    if (runtime.selectedTags.has(code)) runtime.selectedTags.delete(code);
    else runtime.selectedTags.add(code);
    runtime.mediaDraft.tags = [...runtime.selectedTags];
    persistDraft(runtime);
    this.setData({ tagOptions: tagOptions(runtime.selectedTags) });
  },

  toggleMethodInterpretation(event) {
    const runtime = runtimes.get(this);
    const questionId = String(event.currentTarget.dataset.questionid ?? "");
    const eligible = runtime?.session?.result
      ? methodInterpretationEligibleQuestionIds(runtime.session.result)
      : [];
    if (!runtime || this.data.interactionLocked || !eligible.includes(questionId)) return;
    if (runtime.selectedInterpretationQuestionIds.has(questionId)) {
      runtime.selectedInterpretationQuestionIds.delete(questionId);
    } else {
      runtime.selectedInterpretationQuestionIds.add(questionId);
    }
    runtime.mediaDraft.methodCorrectCalculationErrorQuestionIds = [
      ...runtime.selectedInterpretationQuestionIds,
    ];
    persistDraft(runtime);
    this.setData({
      interpretationOptions: interpretationOptions(
        runtime.session as XingceTaskSession,
        runtime.selectedInterpretationQuestionIds,
      ),
    });
  },

  async startVoiceRecording() {
    await beginVoiceRecording(this, null);
  },

  stopVoiceRecording() {
    const runtime = runtimes.get(this);
    if (!runtime || !this.data.isRecording) return;
    runtime.recorder?.stop();
  },

  async openRecordPermission() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
    const granted = await requestRecordSetting();
    if (runtimes.get(this) !== runtime || !currentAccountOwns(runtime)) return;
    this.setData({
      recordPermission: granted ? "granted" : "denied",
      voiceMessage: granted ? "麦克风已开启，可以开始录制。" : "麦克风权限仍未开启。",
    });
  },

  async uploadVoiceSegments() {
    const runtime = runtimes.get(this);
    if (
      !runtime ||
      runtime.disposed ||
      runtime.mediaBusy ||
      runtime.submitting ||
      this.data.isRecording ||
      runtime.mediaDraft.voiceSegments.length === 0
    ) {
      return;
    }
    runtime.mediaBusy = true;
    const epoch = ++runtime.mediaEpoch;
    this.setData({ voiceBusy: true, voiceMessage: "岸岸正在核对录音完整性…" });
    try {
      const segments = await runtime.mediaUploader.uploadAll(
        runtime.mediaDraft.voiceSegments,
        (updated) => {
          runtime.mediaDraft.voiceSegments = updated;
          persistDraft(runtime);
          syncVoiceView(this, runtime);
        },
        () => isMediaCurrent(this, runtime, epoch),
      );
      if (!isMediaCurrent(this, runtime, epoch)) return;
      runtime.mediaDraft.voiceSegments = segments;
      persistDraft(runtime);
      syncVoiceView(this, runtime);
      const allAvailable = segments.every((segment) => segment.state === "available");
      this.setData({
        voiceMessage: allAvailable
          ? "录音已通过安全检查，会随这次复盘提交。"
          : "有录音尚未就绪，本地草稿已保留。",
      });
    } catch (error) {
      if (error instanceof LearningMediaOperationCancelled) return;
      if (!isMediaCurrent(this, runtime, epoch)) return;
      this.setData({
        voiceMessage: error instanceof Error ? error.message : "录音上传未能完成。",
      });
    } finally {
      if (isMediaCurrent(this, runtime, epoch)) {
        runtime.mediaBusy = false;
        this.setData({ voiceBusy: false });
      }
    }
  },

  playVoice(event) {
    const runtime = runtimes.get(this);
    const segmentId = String(event.currentTarget.dataset.id ?? "");
    const segment = runtime?.mediaDraft.voiceSegments.find((item) => item.segmentId === segmentId);
    if (!runtime || !segment || runtime.disposed) return;
    runtime.preview ??= new WechatVoicePreview();
    const epoch = runtime.mediaEpoch;
    try {
      runtime.preview.play(
        segment.localPath,
        () => {
          if (isMediaCurrent(this, runtime, epoch)) this.setData({ playingVoiceId: "" });
        },
        (error) => {
          if (!isMediaCurrent(this, runtime, epoch)) return;
          this.setData({ playingVoiceId: "", voiceMessage: error.message });
        },
      );
      this.setData({ playingVoiceId: this.data.playingVoiceId === segmentId ? "" : segmentId });
    } catch (error) {
      this.setData({
        voiceMessage: error instanceof Error ? error.message : "这段录音暂时无法试听。",
      });
    }
  },

  async transcribeVoice(event) {
    const runtime = runtimes.get(this);
    const segmentId = String(event.currentTarget.dataset.id ?? "");
    const segment = runtime?.mediaDraft.voiceSegments.find((item) => item.segmentId === segmentId);
    if (!runtime || !segment || runtime.disposed || runtime.mediaBusy || this.data.speechBusy)
      return;
    this.setData({ speechBusy: true, voiceMessage: "正在把这段原话转成文字…" });
    try {
      const audioBase64 = await readVoiceFileBase64(segment.localPath);
      const result = await runtime.xfyunApi.speechToText({
        audioBase64,
        durationMilliseconds: segment.durationMilliseconds,
      });
      if (!runtimeIsActive(this, runtime)) return;
      const transcript = result.transcript.trim();
      if (!transcript) throw new Error("这段录音没有识别出清晰文字，可以重录或直接手写。");
      const rawText = [runtime.mediaDraft.rawText.trim(), transcript].filter(Boolean).join("\n");
      runtime.mediaDraft.rawText = rawText;
      persistDraft(runtime);
      this.setData({
        rawText,
        voiceMessage: "听写已放进上方文字，你可以继续修改后再提交。",
      });
    } catch (error) {
      if (runtimeIsActive(this, runtime)) {
        this.setData({
          voiceMessage: error instanceof Error ? error.message : "这段录音暂时无法听写。",
        });
      }
    } finally {
      if (runtimeIsActive(this, runtime)) this.setData({ speechBusy: false });
    }
  },

  async readRawText() {
    const runtime = runtimes.get(this);
    const text = this.data.rawText.trim();
    if (!runtime || runtime.disposed || this.data.speechBusy || !text) return;
    this.setData({ speechBusy: true });
    try {
      const result = await runtime.xfyunApi.textToSpeech(text);
      const localPath = await persistSynthesizedSpeech(result.audioBase64);
      if (!runtimeIsActive(this, runtime)) return;
      runtime.speechPreview ??= new WechatVoicePreview();
      runtime.speechPreview.play(
        localPath,
        () => {
          if (runtimeIsActive(this, runtime)) this.setData({ speechPlaying: false });
        },
        (error) => {
          if (runtimeIsActive(this, runtime)) {
            this.setData({ speechPlaying: false, voiceMessage: error.message });
          }
        },
      );
      this.setData({ speechPlaying: true });
    } catch (error) {
      if (runtimeIsActive(this, runtime)) {
        this.setData({
          voiceMessage: error instanceof Error ? error.message : "这段文字暂时无法朗读。",
        });
      }
    } finally {
      if (runtimeIsActive(this, runtime)) this.setData({ speechBusy: false });
    }
  },

  async deleteVoice(event) {
    const segmentId = String(event.currentTarget.dataset.id ?? "");
    await removeVoiceSegment(this, segmentId, true);
  },

  async rerecordVoice(event) {
    const segmentId = String(event.currentTarget.dataset.id ?? "");
    await beginVoiceRecording(this, segmentId);
  },

  async submitReflection() {
    const runtime = runtimes.get(this);
    const review = runtime?.session?.review;
    if (!runtime || !review || runtime.submitting || runtime.mediaBusy || this.data.isRecording)
      return;
    let pending = loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
    const rawText = this.data.rawText.trim();
    const unreadyVoice = runtime.mediaDraft.voiceSegments.some(
      (segment) => segment.state !== "available",
    );
    if (!pending && unreadyVoice) {
      void wx.showToast({ title: "请先上传或删除未就绪录音", icon: "none" });
      return;
    }
    runtime.submitting = true;
    this.setData({ interactionLocked: true, pendingMessage: "正在写入你的原话复盘…" });
    let returnToTodayForBackgroundCause = false;
    try {
      if (!pending) {
        const voiceArtifactVersionIds = runtime.mediaDraft.voiceSegments.flatMap((segment) =>
          segment.state === "available" && segment.artifactVersionId
            ? [segment.artifactVersionId]
            : [],
        );
        const itemInterpretations = [...runtime.selectedInterpretationQuestionIds].map(
          (questionId) => ({
            questionId,
            interpretation: "method_correct_calculation_error" as const,
          }),
        );
        const idempotencyKey = await createIdempotencyKey("xingce-reflection");
        if (!runtimeIsActive(this, runtime)) return;
        pending = {
          kind: "reflection",
          sessionId: runtime.sessionId,
          reviewId: runtime.reviewId,
          idempotencyKey,
          command: {
            expectedRevision: review.revision,
            rawText,
            tags: [...runtime.selectedTags],
            ...(this.data.strategyNote.trim()
              ? { strategyNote: this.data.strategyNote.trim() }
              : {}),
            ...(this.data.doubtNote.trim() ? { doubtNote: this.data.doubtNote.trim() } : {}),
            ...(voiceArtifactVersionIds.length > 0 ? { voiceArtifactVersionIds } : {}),
            itemInterpretations,
          },
        };
        storePendingXingceReflection(runtime.ownerUserId, pending);
      }
      const response = await runtime.api.submitReflection(
        pending.reviewId,
        pending.command,
        pending.idempotencyKey,
      );
      if (!runtimeIsActive(this, runtime)) {
        if (!currentAccountOwns(runtime)) invalidateForAccountChange(this, runtime);
        return;
      }
      clearPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
      const localPaths = runtime.mediaDraft.voiceSegments.map((segment) => segment.localPath);
      clearReflectionLearningMediaDraft(runtime.ownerUserId, runtime.reviewId);
      runtime.mediaDraft = loadReflectionLearningMediaDraft(runtime.ownerUserId, runtime.reviewId);
      void deleteVoiceFiles(localPaths);
      applySession(this, runtime, response.data);
      returnToTodayForBackgroundCause =
        response.data.review?.state === "cause_candidate_pending";
    } catch (error) {
      if (!runtimeIsActive(this, runtime)) return;
      if (error instanceof ApiClientError && error.statusCode === 409) {
        clearPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
        await reloadAfterConflict(this, runtime);
        return;
      }
      handleWriteError(this, error, "复盘提交结果尚未确认，请重试同一命令。");
    } finally {
      runtime.submitting = false;
    }
    if (returnToTodayForBackgroundCause && runtimeIsActive(this, runtime)) {
      void wx.showToast({ title: "岸岸会在后台整理", icon: "none" });
      try {
        void wx.switchTab({ url: routes.today });
      } catch {
        // 导航失败时保留当前处理页；已有轮询会继续恢复结果，不能重发已成功的写入。
      }
    }
  },

  async refreshCauseCandidate() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed || runtime.submitting) return;
    // 用户手动重试：重置轮询上限
    runtime.causePollCount = 0;
    await pollCauseCandidate(this, runtime, true);
  },

  inputFollowupAnswer(event) {
    if (!this.data.interactionLocked) this.setData({ followupAnswer: event.detail.value });
  },

  async submitCauseFollowup() {
    const runtime = runtimes.get(this);
    const review = runtime?.session?.review;
    if (!runtime || !review || runtime.submitting) return;
    let pending = loadPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId);
    if (!pending) {
      const candidateVersionId = review.aiCandidate.candidateVersionId;
      const answer = this.data.followupAnswer.trim();
      if (review.aiCandidate.state !== "followup_required" || !candidateVersionId) {
        void wx.showToast({ title: "这条追问已经更新，请刷新", icon: "none" });
        return;
      }
      if (!answer) {
        void wx.showToast({ title: "请先回答岸岸的追问", icon: "none" });
        return;
      }
      const idempotencyKey = await createIdempotencyKey("xingce-cause-followup");
      if (!runtimeIsActive(this, runtime)) return;
      pending = {
        kind: "cause_followup",
        sessionId: runtime.sessionId,
        reviewId: runtime.reviewId,
        idempotencyKey,
        command: {
          expectedRevision: review.revision,
          candidateVersionId,
          answer,
        },
      };
      storePendingXingceCauseFollowup(runtime.ownerUserId, pending);
    }
    await submitCauseFollowup(this, runtime);
  },

  inputReplacementCause(event) {
    if (!this.data.interactionLocked) this.setData({ replacementCause: event.detail.value });
  },

  // 结构化改写码单选：再点一次同一 chip 取消选择（选码始终可选）。
  selectReplacementCode(event) {
    const code = String(event.currentTarget.dataset.code ?? "");
    if (this.data.interactionLocked || !isXingceReplacementCauseCode(code)) return;
    const next = selectedReplacementCode(this.data.replacementCodeOptions) === code ? "" : code;
    this.setData({ replacementCodeOptions: replacementCodeOptions(next) });
  },

  async replaceCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    const replacementCause = this.data.replacementCause.trim();
    if (!replacementCause) {
      void wx.showToast({ title: "请写下你认可的错因", icon: "none" });
      return;
    }
    await prepareCauseDecision(
      this,
      runtime,
      "replace",
      replacementCause,
      selectedReplacementCode(this.data.replacementCodeOptions) || undefined,
    );
  },

  async confirmCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    await prepareCauseDecision(this, runtime, "confirm");
  },

  async rejectCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    await prepareCauseDecision(this, runtime, "reject");
  },

  async deferCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    await prepareCauseDecision(this, runtime, "defer");
  },

  returnToday() {
    const plan = runtimes.get(this)?.session?.completion?.plan;
    if (plan) publishLatestTodayDashboard(plan);
    void wx.switchTab({ url: routes.today });
  },

  async startImmediateVerification() {
    const runtime = runtimes.get(this);
    if (
      !runtime?.session ||
      runtime.disposed ||
      runtime.verificationNavigationStarted ||
      this.data.verificationNavigationLocked
    ) {
      return;
    }
    const verification = presentXingceImmediateVerification(runtime.session);
    if (!verification) {
      this.returnToday();
      return;
    }
    runtime.verificationNavigationStarted = true;
    this.setData({ verificationNavigationLocked: true });
    try {
      await wx.navigateTo({ url: verification.url });
    } catch {
      if (!runtimeIsActive(this, runtime)) return;
      runtime.verificationNavigationStarted = false;
      this.setData({ verificationNavigationLocked: false });
      void wx.showToast({ title: "新题暂时打不开，请稍后再试", icon: "none" });
    }
  },

  noop() {},
});

async function beginVoiceRecording(
  page: ReflectionPageInstance,
  replacingSegmentId: string | null,
): Promise<void> {
  const runtime = runtimes.get(page);
  if (
    !runtime ||
    runtime.disposed ||
    runtime.mediaBusy ||
    runtime.submitting ||
    page.data.interactionLocked ||
    page.data.isRecording
  ) {
    return;
  }
  if (
    replacingSegmentId === null &&
    runtime.mediaDraft.voiceSegments.length >= maximumReflectionVoiceSegments
  ) {
    void wx.showToast({ title: "每次复盘最多录制 3 段", icon: "none" });
    return;
  }
  if (
    replacingSegmentId !== null &&
    !runtime.mediaDraft.voiceSegments.some((segment) => segment.segmentId === replacingSegmentId)
  ) {
    return;
  }
  const epoch = ++runtime.mediaEpoch;
  const permission = await ensureRecordPermission();
  if (!isMediaCurrent(page, runtime, epoch)) return;
  if (permission === "denied") {
    page.setData({
      recordPermission: "denied",
      voiceMessage: "麦克风权限已被拒绝。岸岸不会生成假录音，请到设置中明确开启。",
    });
    return;
  }
  runtime.replacingSegmentId = replacingSegmentId;
  runtime.recorder ??= new WechatVoiceRecorder();
  page.setData({
    recordPermission: "granted",
    isRecording: true,
    voiceMessage: "正在录制，单段最长 60 秒。",
  });
  try {
    runtime.recorder.start(
      (recorded) => void finishRecordedVoice(page, runtime, epoch, recorded),
      (error) => {
        if (!isMediaCurrent(page, runtime, epoch)) return;
        runtime.replacingSegmentId = null;
        page.setData({ isRecording: false, voiceMessage: error.message });
      },
    );
  } catch (error) {
    runtime.replacingSegmentId = null;
    page.setData({
      isRecording: false,
      voiceMessage: error instanceof Error ? error.message : "无法启动录音。",
    });
  }
}

async function finishRecordedVoice(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  epoch: number,
  recorded: RecordedVoiceFile,
): Promise<void> {
  if (!isMediaCurrent(page, runtime, epoch)) return;
  page.setData({ isRecording: false, voiceMessage: "正在保存本地录音草稿…" });
  if (
    recorded.byteSize <= 0 ||
    recorded.durationMilliseconds <= 0 ||
    recorded.durationMilliseconds > maximumReflectionVoiceDurationMilliseconds
  ) {
    runtime.replacingSegmentId = null;
    page.setData({ voiceMessage: "这段录音时长无效，请重新录制。" });
    return;
  }
  let localPath = "";
  try {
    localPath = await persistRecordedVoice(recorded.tempFilePath);
    if (!isMediaCurrent(page, runtime, epoch)) {
      await deleteLocalVoice(localPath).catch(() => undefined);
      return;
    }
    const replacingId = runtime.replacingSegmentId;
    const existing = runtime.mediaDraft.voiceSegments;
    const retained = replacingId
      ? existing.filter((segment) => segment.segmentId !== replacingId)
      : existing;
    const totalBytes = retained.reduce((total, segment) => total + segment.byteSize, 0);
    if (totalBytes + recorded.byteSize > maximumReflectionVoiceTotalBytes) {
      await deleteLocalVoice(localPath);
      runtime.replacingSegmentId = null;
      page.setData({ voiceMessage: "本次复盘的录音总大小不能超过 15MB。" });
      return;
    }
    const segment: ReflectionVoiceSegment = {
      segmentId: voiceSegmentId(),
      localPath,
      originalFilename: `reflection-${String(Date.now())}.mp3`,
      mimeType: recorded.mimeType,
      byteSize: recorded.byteSize,
      durationMilliseconds: recorded.durationMilliseconds,
      state: "local",
    };
    let next: ReflectionVoiceSegment[];
    const replaced = replacingId
      ? existing.find((candidate) => candidate.segmentId === replacingId)
      : undefined;
    if (replacingId) {
      next = existing.map((candidate) =>
        candidate.segmentId === replacingId ? segment : candidate,
      );
    } else {
      next = [...existing, segment];
    }
    validateVoiceCollection(next);
    runtime.mediaDraft.voiceSegments = next;
    runtime.replacingSegmentId = null;
    persistDraft(runtime);
    syncVoiceView(page, runtime);
    page.setData({ voiceMessage: "已保存为本地草稿，确认后再上传。" });
    if (replaced) await deleteLocalVoice(replaced.localPath).catch(() => undefined);
  } catch (error) {
    runtime.replacingSegmentId = null;
    if (localPath) await deleteLocalVoice(localPath).catch(() => undefined);
    if (!isMediaCurrent(page, runtime, epoch)) return;
    page.setData({
      voiceMessage: error instanceof Error ? error.message : "录音草稿保存失败。",
    });
  }
}

async function removeVoiceSegment(
  page: ReflectionPageInstance,
  segmentId: string,
  confirm: boolean,
): Promise<void> {
  const runtime = runtimes.get(page);
  const segment = runtime?.mediaDraft.voiceSegments.find((item) => item.segmentId === segmentId);
  if (
    !runtime ||
    !segment ||
    runtime.disposed ||
    runtime.mediaBusy ||
    runtime.submitting ||
    page.data.isRecording
  ) {
    return;
  }
  if (confirm) {
    const decision = await wx.showModal({
      title: "删除这段录音？",
      content: "删除后不会随这次复盘提交，本地录音也会一并清理。",
      confirmText: "确认删除",
    });
    if (!decision.confirm || !currentAccountOwns(runtime)) return;
  }
  try {
    runtime.preview?.stop();
    await deleteLocalVoice(segment.localPath);
    runtime.mediaDraft.voiceSegments = runtime.mediaDraft.voiceSegments.filter(
      (item) => item.segmentId !== segmentId,
    );
    persistDraft(runtime);
    if (runtimes.get(page) !== runtime || !currentAccountOwns(runtime)) return;
    syncVoiceView(page, runtime);
    page.setData({ playingVoiceId: "", voiceMessage: "这段录音已删除。" });
  } catch (error) {
    if (runtimes.get(page) !== runtime || !currentAccountOwns(runtime)) return;
    page.setData({
      voiceMessage: error instanceof Error ? error.message : "本地录音删除失败。",
    });
  }
}

function persistDraft(runtime: ReflectionRuntime): void {
  storeReflectionLearningMediaDraft(runtime.ownerUserId, runtime.reviewId, runtime.mediaDraft);
}

function syncVoiceView(page: ReflectionPageInstance, runtime: ReflectionRuntime): void {
  page.setData({
    voiceSegments: presentReflectionVoiceSegments(runtime.mediaDraft.voiceSegments),
    voiceSummary: presentReflectionVoiceSummary(runtime.mediaDraft.voiceSegments),
  });
}

function hasReflectionDraft(draft: ReflectionLearningMediaDraft): boolean {
  return (
    draft.rawText.length > 0 ||
    draft.strategyNote.length > 0 ||
    draft.doubtNote.length > 0 ||
    draft.tags.length > 0 ||
    draft.methodCorrectCalculationErrorQuestionIds.length > 0 ||
    draft.voiceSegments.length > 0
  );
}

function isMediaCurrent(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  epoch: number,
): boolean {
  return (
    runtimes.get(page) === runtime &&
    !runtime.disposed &&
    runtime.mediaEpoch === epoch &&
    currentAccountOwns(runtime)
  );
}

function voiceSegmentId(): string {
  return `voice-${String(Date.now())}-${Math.random().toString(36).slice(2, 10)}`;
}

async function deleteVoiceFiles(localPaths: readonly string[]): Promise<void> {
  await Promise.all(localPaths.map((path) => deleteLocalVoice(path).catch(() => undefined)));
}

async function submitCauseFollowup(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): Promise<void> {
  if (runtime.submitting) return;
  const pending = loadPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId);
  if (!pending) return;
  runtime.submitting = true;
  page.setData({ interactionLocked: true, pendingMessage: "正在提交追问回答…" });
  try {
    const response = await runtime.api.submitCauseFollowup(
      pending.reviewId,
      pending.command,
      pending.idempotencyKey,
    );
    if (!runtimeIsActive(page, runtime)) {
      if (!currentAccountOwns(runtime)) invalidateForAccountChange(page, runtime);
      return;
    }
    clearPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId);
    applySession(page, runtime, response.data);
  } catch (error) {
    if (!runtimeIsActive(page, runtime)) return;
    if (error instanceof ApiClientError && error.statusCode === 409) {
      clearPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId);
      await reloadAfterConflict(page, runtime);
      return;
    }
    handleWriteError(page, error, "追问回答尚未确认，请重试同一命令。");
  } finally {
    runtime.submitting = false;
  }
}

async function prepareCauseDecision(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  decision: "confirm" | "reject" | "replace" | "defer",
  replacementCause?: string,
  replacementCode?: string,
): Promise<void> {
  const review = runtime.session?.review;
  if (!review) return;
  const candidate = presentXingceCauseCandidate(review.aiCandidate);
  if (!candidate.candidateVersionId || !candidate.canDecide) {
    void wx.showToast({ title: "这条错因建议已经更新，请刷新", icon: "none" });
    return;
  }
  if (decision === "confirm" && !candidate.canConfirm) {
    void wx.showToast({ title: "当前证据不支持确认该错因", icon: "none" });
    return;
  }
  const idempotencyKey = await createIdempotencyKey("xingce-cause");
  if (!runtimeIsActive(page, runtime)) return;
  const pending = {
    kind: "cause_decision" as const,
    sessionId: runtime.sessionId,
    reviewId: runtime.reviewId,
    idempotencyKey,
    command: {
      expectedRevision: review.revision,
      candidateVersionId: candidate.candidateVersionId,
      decision,
      ...(replacementCause ? { replacementCause } : {}),
      // 可选结构化码只随 replace 提交；未选码保持旧命令形状。
      ...(decision === "replace" && replacementCode ? { replacementCode } : {}),
    },
  };
  storePendingXingceCauseDecision(runtime.ownerUserId, pending);
  await decideCause(page, runtime);
}

async function pollCauseCandidate(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  userInitiated: boolean,
): Promise<void> {
  if (!runtimeIsActive(page, runtime) || runtime.submitting) return;
  clearCausePoll(runtime);
  const epoch = ++runtime.requestEpoch;
  if (userInitiated) page.setData({ pendingMessage: "正在刷新错因证据…" });
  try {
    const response = await runtime.api.getSession(runtime.sessionId);
    if (!isCurrent(page, runtime, epoch) || !currentAccountOwns(runtime)) return;
    applySession(page, runtime, response.data);
  } catch (error) {
    if (!isCurrent(page, runtime, epoch)) return;
    page.setData({
      pendingMessage: error instanceof Error ? error.message : "错因状态刷新失败。",
    });
    if (runtime.session?.review?.state === "cause_candidate_pending") {
      scheduleCausePoll(page, runtime);
    }
  }
}

function scheduleCausePoll(page: ReflectionPageInstance, runtime: ReflectionRuntime): void {
  clearCausePoll(runtime);
  if (!runtimeIsActive(page, runtime)) return;
  // 严格结构化错因分析在真实作答轨迹上可能需要约 2 分钟。
  // 这里等待后台异步任务，不把 15 秒误报成分析失败。
  if (runtime.causePollCount >= 140) {
    runtime.causePollCount = 0;
    page.setData({
      pendingMessage: "错因分析暂时没有完成，你的原话已保存，可稍后在今日计划继续。",
    });
    return;
  }
  runtime.causePollCount += 1;
  runtime.causePollTimer = setTimeout(() => {
    runtime.causePollTimer = null;
    void pollCauseCandidate(page, runtime, false);
  }, 1_500);
}

function clearCausePoll(runtime: ReflectionRuntime): void {
  if (runtime.causePollTimer === null) return;
  clearTimeout(runtime.causePollTimer);
  runtime.causePollTimer = null;
}

async function decideCause(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): Promise<void> {
  if (runtime.submitting) return;
  const pending = loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
  if (!pending) return;
  runtime.submitting = true;
  page.setData({ interactionLocked: true, pendingMessage: "正在结算复盘与今日任务…" });
  try {
    const response = await runtime.api.decideCause(
      pending.reviewId,
      pending.command,
      pending.idempotencyKey,
    );
    if (!runtimeIsActive(page, runtime)) {
      if (!currentAccountOwns(runtime)) invalidateForAccountChange(page, runtime);
      return;
    }
    clearPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
    applySession(page, runtime, response.data);
  } catch (error) {
    if (!runtimeIsActive(page, runtime)) return;
    if (error instanceof ApiClientError && error.statusCode === 409) {
      clearPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
      await reloadAfterConflict(page, runtime);
      return;
    }
    handleWriteError(page, error, "错因决定尚未确认，请重试同一命令。");
  } finally {
    runtime.submitting = false;
  }
}

async function reloadAfterConflict(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): Promise<void> {
  try {
    const current = await runtime.api.getSession(runtime.sessionId);
    if (!runtimeIsActive(page, runtime)) {
      if (!currentAccountOwns(runtime)) invalidateForAccountChange(page, runtime);
      return;
    }
    applySession(page, runtime, current.data);
    page.setData({
      errorMessage: "复盘内容已更新；未确认的本地文字仍保留在输入框中。",
      interactionLocked: false,
    });
  } catch (error) {
    handleWriteError(page, error, "无法重新加载最新复盘状态。");
  }
}

function applySession(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  session: XingceTaskSession,
): void {
  const review = session.review;
  if (!review || review.reviewId !== runtime.reviewId || session.sessionId !== runtime.sessionId) {
    throw new Error("复盘记录与当前作答不匹配。");
  }
  runtime.session = session;
  const pendingReflection = loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
  const pendingFollowup = loadPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId);
  const pendingCause = loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
  const existingTags = review.reflection?.tags.filter(isXingceReflectionTag) ?? [];
  const pendingTags = pendingReflection?.command.tags?.filter(isXingceReflectionTag) ?? [];
  const localDraft = runtime.mediaDraft;
  const hasLocalDraft = hasReflectionDraft(localDraft);
  if (pendingReflection || hasLocalDraft) runtime.reviewFormEntered = true;
  const localTags = localDraft.tags.filter(isXingceReflectionTag);
  if (hasLocalDraft) runtime.selectedTags = new Set(localTags);
  else if (pendingTags.length > 0) runtime.selectedTags = new Set(pendingTags);
  else if (existingTags.length > 0) runtime.selectedTags = new Set(existingTags);
  const eligibleInterpretations = new Set(
    session.result ? methodInterpretationEligibleQuestionIds(session.result) : [],
  );
  const pendingInterpretations =
    pendingReflection?.command.itemInterpretations?.map((item) => item.questionId) ?? [];
  const existingInterpretations =
    review.reflection?.itemInterpretations?.map((item) => item.questionId) ?? [];
  const selectedInterpretations = hasLocalDraft
    ? localDraft.methodCorrectCalculationErrorQuestionIds
    : pendingInterpretations.length > 0
      ? pendingInterpretations
      : existingInterpretations;
  runtime.selectedInterpretationQuestionIds = new Set(
    selectedInterpretations.filter((questionId) => eligibleInterpretations.has(questionId)),
  );
  const phase: ReflectionPhase =
    review.state === "reflection_required"
      ? runtime.reviewFormEntered
        ? "reflection"
        : "summary"
      : review.state === "cause_candidate_pending"
        ? "cause_processing"
        : review.state === "cause_followup_required"
          ? "cause_followup"
          : review.state === "cause_decision_required"
            ? "cause_decision"
            : review.state === "cause_evidence_insufficient"
              ? "cause_insufficient"
              : review.state === "cause_agent_terminated"
                ? "cause_terminated"
                : "completed";
  const hasPendingWrite =
    pendingReflection !== null || pendingFollowup !== null || pendingCause !== null;
  const resultView = presentXingceResult(session);
  if (review.state === "reflection_required" && !resultView) {
    throw new Error("本次训练总结与作答记录不完整。");
  }
  const reviewItems = xingceReflectionReviewItems(resultView?.items ?? []);
  const currentReviewIndex = Math.min(
    page.data.currentReviewIndex,
    Math.max(0, reviewItems.length - 1),
  );
  const activeReviewItem = reviewItems[currentReviewIndex] ?? null;
  const completionClosure = presentXingceReflectionClosure(session);
  const immediateVerification = presentXingceImmediateVerification(session);
  page.setData({
    status: "ready",
    phase,
    errorTitle: "",
    errorMessage: "",
    result: resultView,
    reviewItems,
    currentReviewIndex,
    activeReviewItem,
    materialReaderState: materialReaderStateFor(activeReviewItem, page.data.displayPreferences),
    completionClosure,
    hasImmediateVerification: immediateVerification !== null,
    verificationNavigationLocked: runtime.verificationNavigationStarted,
    // 逐题候选 chip 需要真实题号：传入结果条目做位置映射，纯展示不加交互。
    causeCandidate: presentXingceCauseCandidate(review.aiCandidate, resultView?.items ?? []),
    rawText: hasLocalDraft
      ? localDraft.rawText
      : (pendingReflection?.command.rawText ?? review.reflection?.rawText ?? page.data.rawText),
    strategyNote:
      (hasLocalDraft
        ? localDraft.strategyNote
        : (pendingReflection?.command.strategyNote ?? review.reflection?.strategyNote)) ??
      page.data.strategyNote,
    doubtNote:
      (hasLocalDraft
        ? localDraft.doubtNote
        : (pendingReflection?.command.doubtNote ?? review.reflection?.doubtNote)) ??
      page.data.doubtNote,
    tagOptions: tagOptions(runtime.selectedTags),
    interpretationOptions: interpretationOptions(
      session,
      runtime.selectedInterpretationQuestionIds,
    ),
    followupAnswer: pendingFollowup?.command.answer ?? page.data.followupAnswer,
    replacementCause:
      pendingCause?.command.replacementCause ??
      review.causeDecision?.replacementCause ??
      page.data.replacementCause,
    // 待重试的改写命令若带结构化码，恢复对应 chip 选中态。
    replacementCodeOptions: replacementCodeOptions(
      pendingCause?.command.replacementCode ??
        selectedReplacementCode(page.data.replacementCodeOptions),
    ),
    interactionLocked: hasPendingWrite,
    pendingMessage: hasPendingWrite ? "检测到一次结果未知的保存，需要重试刚才那次操作。" : "",
    hasPendingWrite,
  });
  syncVoiceView(page, runtime);
  if (phase === "cause_processing") scheduleCausePoll(page, runtime);
  else clearCausePoll(runtime);
  if (session.completion?.plan) publishLatestTodayDashboard(session.completion.plan);
}

function reviewSwipeBlocked(page: ReflectionPageInstance): boolean {
  return (
    page.data.interactionLocked ||
    page.data.voiceBusy ||
    page.data.isRecording ||
    page.data.replayOpen ||
    page.data.analysisOpen ||
    page.data.materialReaderState.mode === "fullscreen"
  );
}

function selectReviewIndex(page: ReflectionPageInstance, index: number): void {
  const item = page.data.reviewItems[index];
  if (!item || reviewSwipeBlocked(page)) return;
  page.setData({
    currentReviewIndex: index,
    activeReviewItem: item,
    materialReaderState: materialReaderStateFor(item, page.data.displayPreferences),
  });
  if (typeof wx.pageScrollTo === "function") {
    void wx.pageScrollTo({ scrollTop: 0, duration: 0 });
  }
}

function materialReaderStateFor(
  item: XingceResultItemView | null,
  preferences: ContentDisplayPreferences,
): MaterialReaderState {
  const defaultAnchor = item?.material?.anchors[0]?.anchorId ?? "material-start";
  return createMaterialReaderState(
    item?.materialPosition ?? { anchorId: defaultAnchor, scrollTop: 0, zoom: 1 },
    preferences,
  );
}

function tagOptions(selected: ReadonlySet<XingceReflectionTag>): ReflectionPageData["tagOptions"] {
  return xingceReflectionTags.map((code) => ({
    code,
    label: xingceReflectionTagLabels[code],
    selected: selected.has(code),
  }));
}

function replacementCodeOptions(selected: string): ReflectionPageData["replacementCodeOptions"] {
  return xingceReplacementCauseCodes.map((code) => ({
    code,
    label: xingceReplacementCauseCodeLabels[code],
    selected: code === selected,
  }));
}

function selectedReplacementCode(options: ReflectionPageData["replacementCodeOptions"]): string {
  return options.find((option) => option.selected)?.code ?? "";
}

function interpretationOptions(
  session: XingceTaskSession,
  selected: ReadonlySet<string>,
): ReflectionPageData["interpretationOptions"] {
  const result = session.result;
  if (!result) return [];
  const eligible = new Set(methodInterpretationEligibleQuestionIds(result));
  return result.items.flatMap((item, index) =>
    eligible.has(item.questionId)
      ? [
          {
            questionId: item.questionId,
            positionLabel: `第 ${String(index + 1)} 题`,
            answerLabel: `你的答案 ${item.selectedOptionIds.join("、") || "未答"}·正确答案 ${item.correctOptionIds.join("、")}`,
            selected: selected.has(item.questionId),
          },
        ]
      : [],
  );
}

function handleWriteError(page: ReflectionPageInstance, error: unknown, fallback: string): void {
  if (error instanceof ApiClientError && error.statusCode === 401) {
    page.setData({
      status: "unavailable",
      errorTitle: "登录状态已失效",
      errorMessage: "待提交内容仍按原账号隔离保存，重新登录后可以继续。",
      showLoginAction: true,
      pendingMessage: "",
      hasPendingWrite: true,
    });
    return;
  }
  page.setData({
    status: "error",
    errorTitle: "本次保存尚未确认",
    errorMessage: error instanceof Error ? error.message : fallback,
    interactionLocked: true,
    pendingMessage: fallback,
    hasPendingWrite: true,
  });
}

function handleError(page: ReflectionPageInstance, error: unknown, title: string): void {
  const expired = error instanceof ApiClientError && error.statusCode === 401;
  page.setData({
    status: expired ? "unavailable" : "error",
    errorTitle: expired ? "登录状态已失效" : title,
    errorMessage: error instanceof Error ? error.message : "没有拿到可确认状态。",
    showLoginAction: expired,
  });
}

function isCurrent(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  epoch: number,
): boolean {
  return runtimes.get(page) === runtime && !runtime.disposed && runtime.requestEpoch === epoch;
}

function currentAccountOwns(runtime: ReflectionRuntime): boolean {
  return currentAuthSession()?.userId === runtime.ownerUserId;
}

function runtimeIsActive(page: ReflectionPageInstance, runtime: ReflectionRuntime): boolean {
  return runtimes.get(page) === runtime && !runtime.disposed && currentAccountOwns(runtime);
}

function invalidateForAccountChange(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
): void {
  if (runtimes.get(page) !== runtime) return;
  runtime.disposed = true;
  runtime.requestEpoch += 1;
  runtime.mediaEpoch += 1;
  clearCausePoll(runtime);
  runtime.recorder?.dispose();
  runtime.preview?.dispose();
  runtime.speechPreview?.dispose();
  runtimes.delete(page);
  page.setData({
    status: "unavailable",
    errorTitle: "账号已经变更",
    errorMessage: "旧账号的复盘内容已从当前页面清除，请以当前账号重新进入今日计划。",
    showLoginAction: true,
    result: null,
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    causeCandidate: null,
    followupAnswer: "",
    replacementCause: "",
    replacementCodeOptions: replacementCodeOptions(""),
    tagOptions: tagOptions(new Set()),
    interpretationOptions: [],
    interactionLocked: true,
    isRecording: false,
    voiceBusy: false,
    playingVoiceId: "",
    voiceMessage: "",
    voiceSegments: [],
    voiceSummary: presentReflectionVoiceSummary([]),
    speechBusy: false,
    speechPlaying: false,
  });
}

/* ===== Figma 75:2/78:2 · 轨迹回放与动作分析 ===== */

const replayTimers = new WeakMap<object, ReturnType<typeof setInterval>>();

const REPLAY_TICK_MS = 120;

function startReplayTimer(page: ReflectionPageInstance): void {
  stopReplayTimer(page);
  const timer = setInterval(() => {
    const item = page.data.activeReviewItem;
    if (!page.data.replayOpen || !page.data.replayPlaying || !item) {
      stopReplayTimer(page);
      return;
    }
    const duration = page.data.replayDurationMs;
    const next = Math.min(duration, page.data.replayMs + REPLAY_TICK_MS * 4);
    const currentEvent = [...item.actionEvents]
      .reverse()
      .find((entry) => entry.atMs <= next && next - entry.atMs < 4000);
    page.setData({
      replayMs: next,
      replayProgressPercent: duration > 0 ? Math.min(100, Math.round((next / duration) * 100)) : 0,
      replayTimeLabel: `${formatReplayTime(next)} / ${formatReplayTime(duration)}`,
      replayCurrentLabel: formatReplayTime(next),
      replayCurrentEvent: currentEvent
        ? `${formatReplayTime(currentEvent.atMs)} · ${actionEventText(currentEvent)}`
        : "",
      ...(next >= duration ? { replayPlaying: false } : {}),
    });
    if (next >= duration) stopReplayTimer(page);
  }, REPLAY_TICK_MS);
  replayTimers.set(page, timer);
}

function stopReplayTimer(page: ReflectionPageInstance): void {
  const timer = replayTimers.get(page);
  if (timer !== undefined) {
    clearInterval(timer);
    replayTimers.delete(page);
  }
}

function formatReplayTime(milliseconds: number): string {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

/** 把动作事实翻译成可见轨迹描述；只描述发生了什么，不下判断。 */
function actionEventText(entry: XingceResultItemView["actionEvents"][number]): string {
  switch (entry.type) {
    case "option_selected":
      return `选择 ${entry.optionId ?? ""}`.trim();
    case "option_changed":
      return `改答为 ${entry.optionId ?? ""}`.trim();
    case "option_eliminated":
      return `排除选项 ${entry.optionId ?? ""}`.trim();
    case "elimination_undone":
      return `撤销排除 ${entry.optionId ?? ""}`.trim();
    case "confidence_set":
      return "标记作答信心";
    case "draft_edited":
      return "书写草稿";
    case "marked":
      return "标记存疑";
    case "unmarked":
      return "取消存疑";
    case "skipped":
      return "明确跳过本题";
    case "paused":
      return "暂停计时";
    case "resumed":
      return "继续作答";
    case "material_expanded":
      return "展开共享材料";
    case "annotation_added":
      return "题面批注";
    case "annotation_erased":
      return "擦除批注";
    default:
      return "作答动作";
  }
}

/** Figma 78:2：可靠度、依据与时间轨迹全部来自真实事实计数。 */
function presentActionAnalysis(
  item: XingceResultItemView,
): NonNullable<ReflectionPageData["actionAnalysis"]> {
  const strokeCount = item.annotations.length;
  const eraseCount = item.actionEvents.filter((entry) => entry.type === "annotation_erased").length;
  const changeCount = item.actionEvents.filter((entry) => entry.type === "option_changed").length;
  const basisParts = [
    strokeCount > 0 ? `${String(strokeCount)} 笔` : "",
    eraseCount > 0 ? `${String(eraseCount)} 次擦除` : "",
    changeCount > 0 ? `${String(changeCount)} 次回改` : "",
  ].filter(Boolean);
  const evidenceCount = strokeCount + item.actionEvents.length;
  const draftEvents = item.actionEvents.filter((entry) => entry.type === "draft_edited");
  const firstDraft = draftEvents[0];
  const changeEvent = item.actionEvents.find((entry) => entry.type === "option_changed");
  const summaryParts = [
    firstDraft ? `${formatReplayTime(firstDraft.atMs)} 开始书写草稿` : "",
    changeEvent ? `${formatReplayTime(changeEvent.atMs)} 出现改答` : "",
  ].filter(Boolean);
  const significant = item.actionEvents.filter((entry) =>
    [
      "option_selected",
      "option_changed",
      "option_eliminated",
      "draft_edited",
      "marked",
      "paused",
      "material_expanded",
      "annotation_added",
    ].includes(entry.type),
  );
  const rows: NonNullable<ReflectionPageData["actionAnalysis"]>["rows"] = [];
  let lastType = "";
  for (const entry of significant) {
    // 连续同类事件只保留第一条，让轨迹保持可读。
    if (entry.type === lastType && entry.type === "draft_edited") continue;
    lastType = entry.type;
    rows.push({
      timeLabel: formatReplayTime(entry.atMs),
      tone:
        entry.type === "option_changed" || entry.type === "annotation_erased"
          ? "warning"
          : "default",
      text: actionEventText(entry),
    });
    if (rows.length >= 6) break;
  }
  return {
    reliabilityLabel: evidenceCount >= 10 ? "较可靠" : "参考有限",
    basisLabel:
      basisParts.length > 0
        ? `依据 ${basisParts.join("、")}`
        : `依据 ${String(item.actionEvents.length)} 个作答动作`,
    summary: summaryParts.length > 0 ? `草稿显示：${summaryParts.join("；")}。` : "",
    rows,
  };
}
