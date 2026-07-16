import { ApiClient, ApiClientError } from "../../api/client";
import { LearningMediaApi } from "../../api/learning-media";
import { XingceLearningApi, type XingceTaskSession } from "../../api/xingce-learning";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  clearReflectionLearningMediaDraft,
  loadReflectionLearningMediaDraft,
  storeReflectionLearningMediaDraft,
} from "../../features/learning-media/draft-store";
import {
  maximumReflectionVoiceSegments,
  maximumReflectionVoiceTotalBytes,
  validateVoiceCollection,
  type ReflectionLearningMediaDraft,
  type ReflectionVoiceSegment,
} from "../../features/learning-media/contracts";
import {
  deleteLocalVoice,
  ensureRecordPermission,
  persistRecordedVoice,
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
  methodInterpretationEligibleQuestionIds,
  xingceReflectionTagLabels,
  xingceReflectionTags,
  type XingceReflectionTag,
} from "../../features/xingce/real_attempt/contracts";
import {
  presentXingceCauseCandidate,
  presentXingceResult,
  type XingceCauseCandidateView,
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

type ReflectionStatus = "loading" | "ready" | "error" | "unavailable";
type ReflectionPhase =
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
  rawText: string;
  strategyNote: string;
  doubtNote: string;
  tagOptions: Array<{ code: XingceReflectionTag; label: string; selected: boolean }>;
  causeCandidate: XingceCauseCandidateView | null;
  followupAnswer: string;
  replacementCause: string;
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
  interpretationOptions: Array<{
    questionId: string;
    positionLabel: string;
    answerLabel: string;
    selected: boolean;
  }>;
}

interface ReflectionPageCustom {
  loadReview(): Promise<void>;
  retry(): void;
  goLogin(): void;
  exit(): Promise<void>;
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
  deleteVoice(event: WechatMiniprogram.BaseEvent): Promise<void>;
  rerecordVoice(event: WechatMiniprogram.BaseEvent): Promise<void>;
  submitReflection(): Promise<void>;
  refreshCauseCandidate(): Promise<void>;
  inputFollowupAnswer(event: WechatMiniprogram.Input): void;
  submitCauseFollowup(): Promise<void>;
  inputReplacementCause(event: WechatMiniprogram.Input): void;
  confirmCause(): Promise<void>;
  rejectCause(): Promise<void>;
  replaceCause(): Promise<void>;
  deferCause(): Promise<void>;
  returnToday(): void;
}

interface ReflectionRuntime {
  api: XingceLearningApi;
  mediaApi: LearningMediaApi;
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
  replacingSegmentId: string | null;
  causePollTimer: ReturnType<typeof setTimeout> | null;
}

type ReflectionPageInstance = WechatMiniprogram.Page.Instance<
  ReflectionPageData,
  ReflectionPageCustom
>;

const runtimes = new WeakMap<ReflectionPageInstance, ReflectionRuntime>();

Page<ReflectionPageData, ReflectionPageCustom>({
  data: {
    status: "loading",
    phase: "reflection",
    errorTitle: "",
    errorMessage: "",
    showLoginAction: false,
    result: null,
    rawText: "",
    strategyNote: "",
    doubtNote: "",
    tagOptions: tagOptions(new Set()),
    causeCandidate: null,
    followupAnswer: "",
    replacementCause: "",
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
    interpretationOptions: [],
  },

  onLoad(options: Record<string, string | undefined>) {
    const sessionId = options.sessionId?.trim() ?? "";
    const reviewId = options.reviewId?.trim() ?? "";
    if (!sessionId || !reviewId) {
      this.setData({
        status: "error",
        errorTitle: "复盘地址不完整",
        errorMessage: "缺少服务端作答会话或复盘版本，不能写入孤立复盘。",
      });
      return;
    }
    if (environment.dataMode === "prototype") {
      this.setData({
        status: "unavailable",
        errorTitle: "演示数据不能写入复盘",
        errorMessage: "用户原话必须绑定真实作答结果与账号。",
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
      replacingSegmentId: null,
      causePollTimer: null,
    };
    runtimes.set(this, runtime);
    syncVoiceView(this, runtime);
    void this.loadReview();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed) return;
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
        content: "退出不会伪造完成记录，今日任务会保持待复盘状态。",
        confirmText: "仍要退出",
      });
      if (!result.confirm) return;
    }
    void wx.navigateBack();
  },

  inputRawText(event) {
    const runtime = runtimes.get(this);
    if (!runtime || this.data.interactionLocked) return;
    runtime.mediaDraft.rawText = event.detail.value;
    persistDraft(runtime);
    this.setData({ rawText: event.detail.value });
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
    if (!pending && (!rawText || runtime.selectedTags.size === 0)) {
      void wx.showToast({ title: "请写下真实想法并至少选择一个标签", icon: "none" });
      return;
    }
    const unreadyVoice = runtime.mediaDraft.voiceSegments.some(
      (segment) => segment.state !== "available",
    );
    if (!pending && unreadyVoice) {
      void wx.showToast({ title: "请先上传或删除未就绪录音", icon: "none" });
      return;
    }
    runtime.submitting = true;
    this.setData({ interactionLocked: true, pendingMessage: "正在写入你的原话复盘…" });
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
  },

  async refreshCauseCandidate() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.disposed || runtime.submitting) return;
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
        void wx.showToast({ title: "追问候选版本已变更，请刷新", icon: "none" });
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

  async replaceCause() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    const replacementCause = this.data.replacementCause.trim();
    if (!replacementCause) {
      void wx.showToast({ title: "请写下你认可的错因", icon: "none" });
      return;
    }
    await prepareCauseDecision(this, runtime, "replace", replacementCause);
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
    voiceMessage: "正在录制，单段最长 3 分钟。",
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
    recorded.durationMilliseconds > 180_000
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
): Promise<void> {
  const review = runtime.session?.review;
  if (!review) return;
  const candidate = presentXingceCauseCandidate(review.aiCandidate);
  if (!candidate.candidateVersionId || !candidate.canDecide) {
    void wx.showToast({ title: "错因候选版本不可决策，请刷新", icon: "none" });
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
      errorMessage: "服务端复盘版本已更新，已加载权威状态；未确认的本地文字仍保留在输入框中。",
      interactionLocked: false,
    });
  } catch (error) {
    handleWriteError(page, error, "无法重新加载服务端权威复盘状态。");
  }
}

function applySession(
  page: ReflectionPageInstance,
  runtime: ReflectionRuntime,
  session: XingceTaskSession,
): void {
  const review = session.review;
  if (!review || review.reviewId !== runtime.reviewId || session.sessionId !== runtime.sessionId) {
    throw new Error("服务端复盘与当前作答会话不匹配。");
  }
  runtime.session = session;
  const pendingReflection = loadPendingXingceReflection(runtime.ownerUserId, runtime.reviewId);
  const pendingFollowup = loadPendingXingceCauseFollowup(runtime.ownerUserId, runtime.reviewId);
  const pendingCause = loadPendingXingceCauseDecision(runtime.ownerUserId, runtime.reviewId);
  const existingTags = review.reflection?.tags.filter(isXingceReflectionTag) ?? [];
  const pendingTags = pendingReflection?.command.tags.filter(isXingceReflectionTag) ?? [];
  const localDraft = runtime.mediaDraft;
  const hasLocalDraft = hasReflectionDraft(localDraft);
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
      ? "reflection"
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
  page.setData({
    status: "ready",
    phase,
    errorTitle: "",
    errorMessage: "",
    result: presentXingceResult(session),
    causeCandidate: presentXingceCauseCandidate(review.aiCandidate),
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
    interactionLocked: hasPendingWrite,
    pendingMessage: hasPendingWrite ? "检测到一次结果未知的写入，必须按原命令重试。" : "",
    hasPendingWrite,
  });
  syncVoiceView(page, runtime);
  if (phase === "cause_processing") scheduleCausePoll(page, runtime);
  else clearCausePoll(runtime);
  if (session.completion?.plan) publishLatestTodayDashboard(session.completion.plan);
}

function tagOptions(selected: ReadonlySet<XingceReflectionTag>): ReflectionPageData["tagOptions"] {
  return xingceReflectionTags.map((code) => ({
    code,
    label: xingceReflectionTagLabels[code],
    selected: selected.has(code),
  }));
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
      errorMessage: "待提交命令仍按原账号隔离保存，重新登录后可以继续。",
      showLoginAction: true,
      pendingMessage: "",
      hasPendingWrite: true,
    });
    return;
  }
  page.setData({
    status: "error",
    errorTitle: "服务端尚未确认本次写入",
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
    errorMessage: error instanceof Error ? error.message : "服务端没有返回可确认状态。",
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
    tagOptions: tagOptions(new Set()),
    interpretationOptions: [],
    interactionLocked: true,
    isRecording: false,
    voiceBusy: false,
    playingVoiceId: "",
    voiceMessage: "",
    voiceSegments: [],
    voiceSummary: presentReflectionVoiceSummary([]),
  });
}
