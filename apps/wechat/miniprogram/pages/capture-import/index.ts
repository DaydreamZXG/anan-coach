import { currentAuthSession } from "../../auth/session";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import {
  answerIngestionUrl,
  captureImportUrl,
  learningPacketsUrl,
  routes,
} from "../../app/navigation/routes";
import { storeCompanionLaunchContext } from "../../features/companion/context-store";
import {
  CaptureImportController,
  type CaptureImportState,
} from "./features/external-learning/capture-controller";
import { requireExternalLearningGateway } from "../../features/external-learning/contracts";
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
}

interface CatalogOptionView {
  label: string;
  text: string;
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
  },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: loginUrlForReturn(captureImportUrl(options.importId)) });
      return;
    }
    if (options.channel === "open") {
      this.setData({ channelSheetOpen: true });
    }
    try {
      const gateway = requireExternalLearningGateway(session);
      const controller = new CaptureImportController({
        gateway,
        onChange: (state) => {
          if (runtimes.owns(this, controller)) {
            const previous = this.data.state;
            const resetSolutionScroll =
              state.status === "success" &&
              (previous?.status !== "success" ||
                previous.activeQuestionUnit?.questionUnitId !==
                  state.activeQuestionUnit?.questionUnitId);
            this.setData(
              {
                state,
                matchCandidates: presentMatchCandidates(
                  state.activeQuestionUnit?.match?.candidates ?? [],
                ),
                catalogOptions: presentCatalogOptions(
                  state.activeQuestionUnit?.matchedContent?.optionTexts ?? [],
                ),
              },
              () => {
                if (resetSolutionScroll) {
                  wx.nextTick(() => {
                    void wx.pageScrollTo({ scrollTop: 0, duration: 0 });
                  });
                }
              },
            );
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
      });
      const importId = options.importId?.trim();
      if (importId) void controller.load(importId);
    } catch (error) {
      this.setData({ configurationError: externalLearningError(error).message });
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  async chooseCamera() {
    let granted = (await ensureCameraPermission()) === "granted";
    if (!granted) granted = await requestCameraSetting();
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
      this.setData({
        captureStage: "crop",
        cameraReady: false,
        cameraError: "",
        pendingFile: file,
        receiptVisible: false,
        recordSheetOpen: false,
      });
    } catch (error) {
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
    await chooseForReview(this, "album");
  },

  async choosePdf() {
    try {
      const file = await chooseQuestionPdf();
      const controller = runtimes.get(this);
      if (file && controller) await controller.startImport(file);
    } catch {
      void wx.showToast({
        title: "裁剪没打开，直接用原图也行",
        icon: "none",
      });
    }
  },

  async editPendingImage() {
    if (this.imageEditorOpening) return;
    const file = this.data.pendingFile;
    if (!file) return;
    this.imageEditorOpening = true;
    try {
      const edited = await editQuestionImage(file);
      if (edited) this.setData({ pendingFile: edited });
    } catch (error) {
      void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
    } finally {
      this.imageEditorOpening = false;
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
    const granted = await requestCameraSetting();
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
    const snapshot = runtimes.get(this)?.snapshot();
    if (snapshot?.pendingRecovery || snapshot?.workspace) {
      this.retryImport();
      return;
    }
    this.restart();
  },

  retryImport() {
    const controller = runtimes.get(this);
    if (!controller) return;
    if (controller.snapshot().pendingRecovery) void controller.retryPendingImport();
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
    void wx.navigateTo({ url: learningPacketsUrl({ contentVersionId }) });
  },

  openAnswerIngestion() {
    void wx.navigateTo({ url: answerIngestionUrl() });
  },

  askAnan() {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({
        url: loginUrlForReturn(captureImportUrl(this.data.state?.workspace?.importId)),
      });
      return;
    }
    const state = this.data.state;
    const workspace = state?.workspace;
    const unit = state?.activeQuestionUnit;
    if (!state || !workspace || !unit) return;
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
          type: "question",
          id: unit.questionUnitId,
          version: workspace.importId,
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

async function chooseForReview(page: CaptureImportPage, source: "camera" | "album") {
  try {
    const file = await chooseQuestionImage(source);
    if (file) {
      page.setData({
        captureStage: "crop",
        pendingFile: file,
        receiptVisible: false,
        recordSheetOpen: false,
      });
    }
  } catch (error) {
    void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
  }
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
