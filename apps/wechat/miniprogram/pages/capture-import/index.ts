import { currentAuthSession } from "../../auth/session";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { PageEffectOwner, type PageEffectLease } from "../../app/runtime/page-effect-owner";
import {
  answerIngestionUrl,
  captureImportUrl,
  learningPacketsUrl,
  questionBookImportUrl,
  routes,
} from "../../app/navigation/routes";
import { ApiClient } from "../../api/client";
import { PlanningApi } from "../../api/planning";
import { environment } from "../../config/environment";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import {
  CaptureImportController,
  type CaptureImportState,
} from "./features/external-learning/capture-controller";
import {
  requireExternalLearningGateway,
  requirePrivateQuestionBookGateway,
} from "../../features/external-learning/contracts";
import {
  preferredQuestionBookPlanTaskId,
  QuestionBookFlow,
  type QuestionBookFlowState,
} from "./question-book-flow";
import { externalLearningError } from "./shared/features/external-learning/error";
import {
  presentMatchCandidates,
  type MatchCandidateView,
} from "./features/external-learning/match-presentation";
import { ExternalLearningPageRuntimeRegistry } from "./shared/features/external-learning/page-runtime";
import {
  chooseQuestionImage,
  chooseQuestionPdf,
  editQuestionImage,
  ensureCameraPermission,
  requestCameraSetting,
  takeQuestionPhoto,
} from "./shared/features/external-learning/platform";
import type { LocalImportFile } from "../../features/external-learning/contracts";

interface CaptureImportPageData {
  state: CaptureImportState | null;
  configurationError: string;
  mode: "search" | "import";
  captureStage: "camera" | "crop";
  cameraVisible: boolean;
  cameraReady: boolean;
  cameraError: string;
  permissionDenied: boolean;
  pendingFile: LocalImportFile | null;
  recordSheetOpen: boolean;
  receiptVisible: boolean;
  channelSheetOpen: boolean;
  matchCandidates: MatchCandidateView[];
  catalogOptions: CatalogOptionView[];
  batchProgress: BatchProgressView;
  bookMode: boolean;
  bookId: string;
  questionBookState: QuestionBookFlowState | null;
  questionBookPlanTaskId: string;
  questionBookPlanStatus: "idle" | "loading" | "ready" | "unavailable" | "error";
  questionBookPlanMessage: string;
}

interface CatalogOptionView {
  label: string;
  text: string;
}

interface BatchProgressView {
  visible: boolean;
  title: string;
  detail: string;
}

interface CaptureImportPageCustom {
  imageEditorOpening: boolean;
  chooseCamera(): Promise<void>;
  retryCamera(): Promise<void>;
  handleCameraInit(): void;
  handleCameraError(
    event: WechatMiniprogram.CustomEvent<{ errCode?: number; errMsg?: string }>,
  ): void;
  chooseAlbum(): Promise<void>;
  choosePdf(): Promise<void>;
  editPendingImage(): Promise<void>;
  confirmPendingImage(): void;
  discardPendingImage(): void;
  retryCameraPermission(): Promise<void>;
  chooseTrack(event: WechatMiniprogram.BaseEvent): void;
  chooseMode(event: WechatMiniprogram.BaseEvent): void;
  inputSegment(event: WechatMiniprogram.Input): void;
  selectMatch(event: WechatMiniprogram.BaseEvent): void;
  selectQuestionUnit(event: WechatMiniprogram.BaseEvent): void;
  requestAIAnswer(): void;
  confirmAIAnswer(): void;
  confirmImport(): void;
  handleErrorAction(): void;
  retryImport(): void;
  restart(): void;
  openLearningPackets(): void;
  openAnswerIngestion(): void;
  askAnan(): void;
  openRecordSheet(): void;
  closeRecordSheet(): void;
  finishViewing(): void;
  startQuestionBookPractice(): Promise<void>;
  retryQuestionBookSave(): void;
  openQuestionBookAdjustment(): void;
  openQuestionBookLibrary(): void;
  openChannelSheet(): void;
  closeChannelSheet(): void;
  goBack(): void;
  noop(): void;
}

type CaptureImportPage = WechatMiniprogram.Page.Instance<
  CaptureImportPageData,
  CaptureImportPageCustom
>;

const runtimes = new ExternalLearningPageRuntimeRegistry<
  CaptureImportPage,
  CaptureImportController
>();
const captureEffects = new WeakMap<CaptureImportPage, PageEffectOwner>();
const questionBookFlows = new WeakMap<CaptureImportPage, QuestionBookFlow>();

interface CaptureEffect {
  owner: PageEffectOwner;
  lease: PageEffectLease;
}

Page<CaptureImportPageData, CaptureImportPageCustom>({
  imageEditorOpening: false,

  data: {
    state: null,
    configurationError: "",
    mode: "search",
    captureStage: "camera",
    cameraVisible: true,
    cameraReady: false,
    cameraError: "",
    permissionDenied: false,
    pendingFile: null,
    recordSheetOpen: false,
    receiptVisible: false,
    channelSheetOpen: false,
    matchCandidates: [],
    catalogOptions: [],
    batchProgress: emptyBatchProgress(),
    bookMode: false,
    bookId: "",
    questionBookState: null,
    questionBookPlanTaskId: "",
    questionBookPlanStatus: "idle",
    questionBookPlanMessage: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const bookMode = options.mode === "book";
    const bookId = options.bookId?.trim() ?? "";
    this.setData({ bookMode, bookId, mode: bookMode ? "import" : "search" });
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({
        url: loginUrlForReturn(
          bookMode
            ? questionBookImportUrl({ importId: options.importId, bookId: options.bookId })
            : captureImportUrl(options.importId),
        ),
      });
      return;
    }
    activateCaptureEffects(this, session);
    if (options.channel === "open") {
      this.setData({ channelSheetOpen: true });
    }
    try {
      const gateway = requireExternalLearningGateway(session);
      const questionBookGateway = requirePrivateQuestionBookGateway(gateway);
      const questionBookFlow = new QuestionBookFlow({
        gateway: questionBookGateway,
        onChange: (state) => {
          if (questionBookFlows.get(this) !== questionBookFlow) return;
          this.setData({
            questionBookState: state,
            bookId: state.book?.bookId ?? this.data.bookId,
          });
          if (state.book && this.data.questionBookPlanStatus === "idle") {
            void loadQuestionBookPlan(this, session.accessToken);
          }
        },
      });
      questionBookFlows.set(this, questionBookFlow);
      const controller = new CaptureImportController({
        gateway,
        bookMode,
        onChange: (state) => {
          if (runtimes.owns(this, controller)) {
            const previous = this.data.state;
            const activeQuestionChanged =
              previous?.activeQuestionUnit?.questionUnitId !==
              state.activeQuestionUnit?.questionUnitId;
            const resetSolutionScroll =
              activeQuestionChanged && (state.status === "review" || state.status === "success");
            this.setData(
              {
                state,
                matchCandidates: presentMatchCandidates(
                  state.activeQuestionUnit?.match?.candidates ?? [],
                ),
                catalogOptions: presentCatalogOptions(
                  state.activeQuestionUnit?.matchedContent?.optionTexts ?? [],
                ),
                batchProgress: presentBatchProgress(state),
              },
              () => {
                if (resetSolutionScroll) {
                  wx.nextTick(() => {
                    void wx.pageScrollTo({ scrollTop: 0, duration: 0 });
                  });
                }
              },
            );
            if (bookMode) void syncQuestionBookFromCapture(this, state);
          }
        },
      });
      runtimes.attach(this, controller);
      const state = controller.snapshot();
      this.setData({
        state,
        configurationError: "",
        matchCandidates: presentMatchCandidates(state.activeQuestionUnit?.match?.candidates ?? []),
        catalogOptions: presentCatalogOptions(
          state.activeQuestionUnit?.matchedContent?.optionTexts ?? [],
        ),
        batchProgress: presentBatchProgress(state),
      });
      const importId = options.importId?.trim();
      if (importId) {
        void controller.load(importId);
      } else if (bookId) {
        void questionBookFlow.loadBook(bookId).then((book) => {
          if (book && runtimes.owns(this, controller)) void controller.load(book.importId);
        });
      }
    } catch (error) {
      this.setData({ configurationError: externalLearningError(error).message });
    }
  },

  onShow() {
    const session = currentAuthSession();
    if (!session) {
      captureEffects.get(this)?.unload();
      captureEffects.delete(this);
      void wx.reLaunch({
        url: loginUrlForReturn(
          this.data.bookMode
            ? questionBookImportUrl({
                importId: this.data.state?.workspace?.importId,
                bookId: this.data.bookId || undefined,
              })
            : captureImportUrl(this.data.state?.workspace?.importId),
        ),
      });
      return;
    }
    activateCaptureEffects(this, session);
    this.imageEditorOpening = false;
  },

  onHide() {
    captureEffects.get(this)?.hide();
    this.imageEditorOpening = false;
  },

  onUnload() {
    captureEffects.get(this)?.unload();
    captureEffects.delete(this);
    this.imageEditorOpening = false;
    questionBookFlows.get(this)?.dispose();
    questionBookFlows.delete(this);
    runtimes.detach(this);
  },

  async chooseCamera() {
    const effect = beginCaptureEffect(this, "picker");
    if (!effect) return;
    let granted = (await ensureCameraPermission()) === "granted";
    if (!captureEffectIsCurrent(this, effect)) return;
    if (!granted) {
      granted = await requestCameraSetting();
      if (!captureEffectIsCurrent(this, effect)) return;
    }
    if (!granted) {
      this.setData({ permissionDenied: true });
      return;
    }
    if (!this.data.cameraReady) {
      this.setData({
        permissionDenied: false,
        cameraError: "相机正在启动，请稍候再拍。",
      });
      return;
    }
    try {
      const file = await takeQuestionPhoto();
      if (!captureEffectIsCurrent(this, effect)) return;
      this.setData({
        captureStage: "crop",
        cameraReady: false,
        cameraError: "",
        pendingFile: file,
        receiptVisible: false,
        recordSheetOpen: false,
      });
    } catch (error) {
      if (!captureEffectIsCurrent(this, effect)) return;
      const message = externalLearningError(error).message;
      if (cameraPermissionWasDenied(error)) {
        this.setData({ permissionDenied: true, cameraReady: false, cameraError: "" });
        return;
      }
      this.setData({ cameraError: message });
    }
  },

  async retryCamera() {
    let granted = (await ensureCameraPermission()) === "granted";
    if (!granted) granted = await requestCameraSetting();
    if (!granted) {
      this.setData({
        permissionDenied: true,
        cameraVisible: false,
        cameraReady: false,
        cameraError: "",
      });
      return;
    }
    this.setData({
      permissionDenied: false,
      cameraVisible: false,
      cameraReady: false,
      cameraError: "",
    });
    wx.nextTick(() => {
      this.setData({ cameraVisible: true });
    });
  },

  handleCameraInit() {
    this.setData({ cameraReady: true, cameraError: "", permissionDenied: false });
  },

  handleCameraError(event) {
    const detail = event.detail;
    if (detail.errCode === 10001 || cameraPermissionWasDenied({ errMsg: detail.errMsg ?? "" })) {
      this.setData({ permissionDenied: true, cameraReady: false, cameraError: "" });
      return;
    }
    this.setData({
      cameraReady: false,
      cameraError: "相机没启动成功，检查一下系统相机权限再试一次吧。",
    });
  },

  async chooseAlbum() {
    const effect = beginCaptureEffect(this, "picker");
    if (effect) await chooseForReview(this, "album", effect);
  },

  async choosePdf() {
    const effect = beginCaptureEffect(this, "picker");
    if (!effect) return;
    try {
      const file = await chooseQuestionPdf();
      if (!captureEffectIsCurrent(this, effect)) return;
      const controller = runtimes.get(this);
      if (file && controller) await controller.startImport(file);
    } catch (error) {
      if (!captureEffectIsCurrent(this, effect)) return;
      const message =
        error instanceof Error && error.message === "请选择 PDF 文件。"
          ? error.message
          : "PDF 先发到文件传输助手再选";
      void wx.showToast({
        title: message,
        icon: "none",
      });
    }
  },

  async editPendingImage() {
    if (this.imageEditorOpening) return;
    const file = this.data.pendingFile;
    if (!file) return;
    const effect = beginCaptureEffect(this, "crop");
    if (!effect) return;
    this.imageEditorOpening = true;
    try {
      const edited = await editQuestionImage(file);
      if (!captureEffectIsCurrent(this, effect)) return;
      if (edited) this.setData({ pendingFile: edited });
    } catch (error) {
      if (!captureEffectIsCurrent(this, effect)) return;
      void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
    } finally {
      if (captureEffectIsCurrent(this, effect)) this.imageEditorOpening = false;
    }
  },

  confirmPendingImage() {
    const file = this.data.pendingFile;
    const controller = runtimes.get(this);
    if (!file || !controller) return;
    this.setData({
      captureStage: "camera",
      pendingFile: null,
      receiptVisible: false,
      recordSheetOpen: false,
    });
    void controller.startImport(file);
  },

  discardPendingImage() {
    this.setData({
      captureStage: "camera",
      cameraVisible: true,
      cameraReady: false,
      cameraError: "",
      pendingFile: null,
    });
  },

  async retryCameraPermission() {
    const effect = beginCaptureEffect(this, "picker");
    if (!effect) return;
    const granted = await requestCameraSetting();
    if (!captureEffectIsCurrent(this, effect)) return;
    this.setData({
      permissionDenied: !granted,
      cameraReady: false,
      cameraError: "",
    });
  },

  chooseTrack(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "xingce" || value === "essay") runtimes.get(this)?.setTrack(value);
  },

  chooseMode(event) {
    if (this.data.bookMode) return;
    const mode = String(event.currentTarget.dataset.mode ?? "");
    if (mode === "search" || mode === "import") this.setData({ mode });
  },

  inputSegment(event) {
    runtimes
      .get(this)
      ?.updateSegment(String(event.currentTarget.dataset.id ?? ""), event.detail.value);
  },

  selectMatch(event) {
    runtimes.get(this)?.selectMatch(String(event.currentTarget.dataset.id ?? ""));
  },

  selectQuestionUnit(event) {
    runtimes.get(this)?.selectQuestionUnit(Number(event.currentTarget.dataset.index));
  },

  requestAIAnswer() {
    void runtimes.get(this)?.requestAIAnswer();
  },

  confirmAIAnswer() {
    void runtimes.get(this)?.confirmAIAnswer();
  },

  confirmImport() {
    void runtimes.get(this)?.confirm();
  },

  handleErrorAction() {
    const controller = runtimes.get(this);
    if (!controller) return;
    const snapshot = controller.snapshot();
    if (snapshot.pendingRecovery || snapshot.workspace) void controller.retryPendingOperation();
    else this.restart();
  },

  retryImport() {
    const controller = runtimes.get(this);
    if (!controller) return;
    if (controller.snapshot().pendingRecovery) void controller.retryPendingOperation();
    else void controller.refresh();
  },

  restart() {
    runtimes.get(this)?.reset();
    this.setData({
      captureStage: "camera",
      cameraVisible: true,
      cameraReady: false,
      cameraError: "",
      permissionDenied: false,
      pendingFile: null,
      recordSheetOpen: false,
      receiptVisible: false,
      channelSheetOpen: false,
    });
  },

  openLearningPackets() {
    const contentVersionId =
      this.data.state?.activeQuestionUnit?.match?.confirmedContentVersionId?.trim() ?? "";
    if (!contentVersionId) {
      void wx.showToast({ title: "先确认一下匹配的题目吧", icon: "none" });
      return;
    }
    navigateFromRecordSheet(this, learningPacketsUrl({ contentVersionId }));
  },

  openAnswerIngestion() {
    navigateFromRecordSheet(this, answerIngestionUrl());
  },

  askAnan() {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({
        url: loginUrlForReturn(
          this.data.bookMode
            ? questionBookImportUrl({
                importId: this.data.state?.workspace?.importId,
                bookId: this.data.bookId || undefined,
              })
            : captureImportUrl(this.data.state?.workspace?.importId),
        ),
      });
      return;
    }
    const state = this.data.state;
    const workspace = state?.workspace;
    const unit = state?.activeQuestionUnit;
    const recognitionRevisionId = workspace?.recognition?.recognitionRevisionId.trim() ?? "";
    if (!state || !workspace || !unit || !recognitionRevisionId) {
      void wx.showToast({ title: "题面还没确认好，请稍后再问", icon: "none" });
      return;
    }
    const questionText = state.segments
      .filter((segment) => unit.segmentIds.includes(segment.segmentId))
      .map((segment) => segment.text)
      .join("\n")
      .trim();
    const answer = unit.referenceAnswer?.answerText ?? unit.aiAnswer?.answerText ?? "暂无答案";
    storeCompanionLaunchContext(session.userId, {
      sourceLabel: "当前拍照搜题",
      title: `第 ${String(unit.ordinal)} 题`,
      summary: `${questionText.slice(0, 240)}；当前解析答案：${answer}`,
      suggestedQuestion: "结合当前题面和解析，逐步讲清楚关键判断，并指出最容易出错的地方。",
      routeId: "pages/capture-import/index",
      workspace: "training",
      objectRefs: [
        {
          type: "external_question",
          id: unit.questionUnitId,
          version: recognitionRevisionId,
        },
      ],
      taskMode: state.learningTrack === "essay" ? "essay" : "practice",
      taskStage: "reviewing",
    });
    void wx.switchTab({ url: routes.anan });
  },

  openRecordSheet() {
    this.setData({ recordSheetOpen: true });
  },

  closeRecordSheet() {
    this.setData({ recordSheetOpen: false });
  },

  finishViewing() {
    this.setData({ recordSheetOpen: false, receiptVisible: true });
  },

  async startQuestionBookPractice() {
    const flow = questionBookFlows.get(this);
    const planTaskId = this.data.questionBookPlanTaskId;
    if (!flow || !planTaskId) {
      this.openQuestionBookAdjustment();
      return;
    }
    const target = await flow.startPractice(planTaskId);
    if (!target) return;
    if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
    else void wx.navigateTo({ url: target.url });
  },

  retryQuestionBookSave() {
    const state = this.data.state;
    const flow = questionBookFlows.get(this);
    if (!state?.workspace || !flow) return;
    void flow.retrySave(state.workspace, questionBookTitle(this));
  },

  openQuestionBookAdjustment() {
    void wx.navigateTo({ url: routes.dayAdjustment });
  },

  openQuestionBookLibrary() {
    void wx.navigateTo({ url: routes.privateKnowledge });
  },

  openChannelSheet() {
    this.setData({ channelSheetOpen: true });
  },

  closeChannelSheet() {
    this.setData({ channelSheetOpen: false });
  },

  goBack() {
    if (this.data.permissionDenied) {
      this.setData({ permissionDenied: false });
      return;
    }
    if (this.data.captureStage === "crop") {
      this.discardPendingImage();
      return;
    }
    if (this.data.receiptVisible) {
      this.setData({ receiptVisible: false });
      return;
    }
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.switchTab({ url: routes.today });
  },

  noop() {},
});

async function syncQuestionBookFromCapture(
  page: CaptureImportPage,
  state: CaptureImportState,
): Promise<void> {
  const flow = questionBookFlows.get(page);
  const workspace = state.workspace;
  if (!flow || !workspace || workspace.recognition?.state !== "confirmed") return;
  await flow.syncWorkspace(workspace, questionBookTitle(page));
}

async function loadQuestionBookPlan(page: CaptureImportPage, accessToken: string): Promise<void> {
  if (page.data.questionBookPlanStatus !== "idle") return;
  page.setData({
    questionBookPlanStatus: "loading",
    questionBookPlanTaskId: "",
    questionBookPlanMessage: "正在接入你今天的训练安排…",
  });
  try {
    const response = await new PlanningApi(
      new ApiClient(environment.apiBaseUrl, accessToken),
    ).getToday();
    if (!questionBookFlows.has(page)) return;
    const planTaskId = preferredQuestionBookPlanTaskId(response.data);
    page.setData({
      questionBookPlanStatus: planTaskId ? "ready" : "unavailable",
      questionBookPlanTaskId: planTaskId ?? "",
      questionBookPlanMessage: planTaskId
        ? "沿用今天的行测训练，做完继续进入原有错因和岸岸反馈闭环。"
        : "今天还没有可接入的行测练习，先调整今天，题本已经替你保存好了。",
    });
  } catch {
    if (!questionBookFlows.has(page)) return;
    page.setData({
      questionBookPlanStatus: "error",
      questionBookPlanTaskId: "",
      questionBookPlanMessage: "今天的训练安排暂时没读到，题本已经保存，可以稍后再练。",
    });
  }
}

function questionBookTitle(page: CaptureImportPage): string {
  return (
    page.data.state?.selectedFile?.displayName ??
    page.data.questionBookState?.book?.title ??
    "我的名师题本"
  );
}

async function chooseForReview(
  page: CaptureImportPage,
  source: "camera" | "album",
  effect: CaptureEffect,
) {
  try {
    const file = await chooseQuestionImage(source);
    if (file && captureEffectIsCurrent(page, effect)) {
      page.setData({
        captureStage: "crop",
        pendingFile: file,
        receiptVisible: false,
        recordSheetOpen: false,
      });
    }
  } catch (error) {
    if (!captureEffectIsCurrent(page, effect)) return;
    void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
  }
}

function activateCaptureEffects(
  page: CaptureImportPage,
  session: NonNullable<ReturnType<typeof currentAuthSession>>,
): PageEffectOwner {
  const previous = captureEffects.get(page);
  const owner = previous?.matches(session) ? previous : new PageEffectOwner(session);
  if (previous && previous !== owner) previous.unload();
  captureEffects.set(page, owner);
  owner.show(session);
  return owner;
}

function beginCaptureEffect(page: CaptureImportPage, lane: string): CaptureEffect | null {
  const owner = captureEffects.get(page);
  const lease = owner?.begin(lane, currentAuthSession()) ?? null;
  return owner && lease ? { owner, lease } : null;
}

function captureEffectIsCurrent(page: CaptureImportPage, effect: CaptureEffect): boolean {
  return (
    captureEffects.get(page) === effect.owner &&
    effect.owner.owns(effect.lease, currentAuthSession())
  );
}

function navigateFromRecordSheet(page: CaptureImportPage, url: string): void {
  const owner = captureEffects.get(page);
  page.setData({ recordSheetOpen: false }, () => {
    if (owner?.active(currentAuthSession())) void wx.navigateTo({ url });
  });
}

function cameraPermissionWasDenied(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const rawMessage = (error as { errMsg?: unknown }).errMsg;
  const message = typeof rawMessage === "string" ? rawMessage.toLowerCase() : "";
  return (
    message.includes("auth deny") || message.includes("permission") || message.includes("authorize")
  );
}

function presentCatalogOptions(optionTexts: readonly string[]): CatalogOptionView[] {
  return optionTexts.map((raw, index) => {
    const fallbackLabel = String.fromCharCode(65 + index);
    const match = raw.trim().match(/^([A-ZＡ-Ｚ])[\s、.．:：)）]+(.*)$/u);
    return {
      label: match?.[1]?.normalize("NFKC") ?? fallbackLabel,
      text: (match?.[2] ?? raw).trim(),
    };
  });
}

function emptyBatchProgress(): BatchProgressView {
  return { visible: false, title: "", detail: "" };
}

function presentBatchProgress(state: CaptureImportState): BatchProgressView {
  const units = state.workspace?.questionUnits ?? [];
  if (units.length <= 1) return emptyBatchProgress();
  const ready = units.filter((unit) => unit.state === "ready").length;
  const ambiguous = units.filter((unit) => unit.state === "match_review").length;
  const unmatched = units.filter((unit) => unit.state === "unmatched").length;
  const details = [
    ambiguous > 0 ? `只需核对 ${String(ambiguous)} 题` : "",
    unmatched > 0 ? `${String(unmatched)} 题未命中` : "",
  ].filter(Boolean);
  return {
    visible: true,
    title: `已自动整理 ${String(ready)} / ${String(units.length)} 题`,
    detail: details.join("·") || "全部已匹配，可以直接查看",
  };
}
