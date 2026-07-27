import { currentAuthSession } from "../../auth/session";
import { ApiClient, ApiClientError } from "../../api/client";
import { ILinkChannelApi, type HubBotAuthorizationView } from "../../api/ilink-channel";
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
} from "../../features/external-learning/capture-controller";
import { requireExternalLearningGateway } from "../../features/external-learning/contracts";
import { externalLearningError } from "../../features/external-learning/error";
import {
  presentMatchCandidates,
  type MatchCandidateView,
} from "../../features/external-learning/match-presentation";
import { ExternalLearningPageRuntimeRegistry } from "../../features/external-learning/page-runtime";
import {
  chooseQuestionImage,
  chooseQuestionPdf,
  editQuestionImage,
  ensureCameraPermission,
  requestCameraSetting,
  takeQuestionPhoto,
} from "../../features/external-learning/platform";
import type { LocalImportFile } from "../../features/external-learning/contracts";
import { createIdempotencyKey } from "../../platform/command-id";

interface CaptureImportPageData {
  state: CaptureImportState | null;
  configurationError: string;
  mode: "search" | "import";
  channelView: HubBotAuthorizationView | null;
  channelStatus: "hidden" | "loading" | "ready" | "unavailable" | "error";
  channelAutoOpen: boolean;
  channelError: string;
  channelWriting: boolean;
  channelPolling: boolean;
  captureStage: "camera" | "crop";
  cameraReady: boolean;
  cameraError: string;
  permissionDenied: boolean;
  pendingFile: LocalImportFile | null;
  recordSheetOpen: boolean;
  receiptVisible: boolean;
  channelSheetOpen: boolean;
  matchCandidates: MatchCandidateView[];
}

interface CaptureImportPageCustom {
  chooseCamera(): Promise<void>;
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
  loadChannel(): Promise<void>;
  startChannelAuthorization(): Promise<void>;
  replaceChannelAuthorization(): Promise<void>;
  pollChannelAuthorization(): Promise<void>;
  revokeChannelLink(): Promise<void>;
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
const channelPollTimers = new WeakMap<CaptureImportPage, ReturnType<typeof setTimeout>>();

Page<CaptureImportPageData, CaptureImportPageCustom>({
  data: {
    state: null,
    configurationError: "",
    mode: "search",
    channelView: null,
    channelStatus: "hidden",
    channelAutoOpen: false,
    channelError: "",
    channelWriting: false,
    channelPolling: false,
    captureStage: "camera",
    cameraReady: false,
    cameraError: "",
    permissionDenied: false,
    pendingFile: null,
    recordSheetOpen: false,
    receiptVisible: false,
    channelSheetOpen: false,
    matchCandidates: [],
  },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: loginUrlForReturn(captureImportUrl(options.importId)) });
      return;
    }
    void this.loadChannel();
    if (options.channel === "open") {
      // 「我的」页绑定入口直达：通道状态就绪后自动展开绑定/管理面板。
      this.setData({ channelAutoOpen: true });
    }
    try {
      const gateway = requireExternalLearningGateway(session);
      const controller = new CaptureImportController({
        gateway,
        onChange: (state) => {
          if (runtimes.owns(this, controller)) {
            this.setData({
              state,
              matchCandidates: presentMatchCandidates(
                state.activeQuestionUnit?.match?.candidates ?? [],
              ),
            });
          }
        },
      });
      runtimes.attach(this, controller);
      const state = controller.snapshot();
      this.setData({
        state,
        configurationError: "",
        matchCandidates: presentMatchCandidates(state.activeQuestionUnit?.match?.candidates ?? []),
      });
      const importId = options.importId?.trim();
      if (importId) void controller.load(importId);
    } catch (error) {
      this.setData({ configurationError: externalLearningError(error).message });
    }
  },

  onUnload() {
    stopChannelPolling(this);
    runtimes.detach(this);
  },

  onShow() {
    if (
      this.data.channelSheetOpen &&
      this.data.channelView?.authorizationId &&
      ["waiting_scan", "scanned"].includes(this.data.channelView.state)
    ) {
      scheduleChannelPoll(this);
    }
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
      cameraError: "相机没有启动成功，请检查系统相机权限后重试。",
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
    } catch (error) {
      void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
    }
  },

  async editPendingImage() {
    const file = this.data.pendingFile;
    if (!file) return;
    try {
      const edited = await editQuestionImage(file);
      if (edited) this.setData({ pendingFile: edited });
    } catch (error) {
      void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
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
      void wx.showToast({ title: "请先确认匹配的题目", icon: "none" });
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
    if (
      this.data.channelView?.authorizationId &&
      ["waiting_scan", "scanned"].includes(this.data.channelView.state)
    ) {
      scheduleChannelPoll(this);
    }
  },

  closeChannelSheet() {
    stopChannelPolling(this);
    this.setData({ channelSheetOpen: false });
  },

  async loadChannel() {
    const session = currentAuthSession();
    if (!session) return;
    this.setData({ channelStatus: "loading", channelError: "" });
    try {
      const response = await new ILinkChannelApi(
        new ApiClient(undefined, session.accessToken),
      ).getHubBot();
      const view = response.data;
      this.setData({
        channelView: view,
        channelStatus: "ready",
        channelError: "",
      });
      if (this.data.channelAutoOpen) {
        this.setData({ channelAutoOpen: false });
        this.openChannelSheet();
      } else if (
        this.data.channelSheetOpen &&
        view.authorizationId &&
        ["waiting_scan", "scanned"].includes(view.state)
      ) {
        scheduleChannelPoll(this);
      }
    } catch (error) {
      this.setData({
        channelStatus:
          error instanceof ApiClientError && error.statusCode === 503 ? "unavailable" : "error",
        channelError:
          error instanceof ApiClientError
            ? `${error.message}（请求 ${error.requestId}）`
            : "微信岸岸的连接状态没有加载成功，请重新读取。",
      });
      if (this.data.channelAutoOpen) {
        this.setData({ channelAutoOpen: false, channelSheetOpen: true });
      }
    }
  },

  async startChannelAuthorization() {
    await writeChannel(this, false);
  },

  async replaceChannelAuthorization() {
    await writeChannel(this, true);
  },

  async pollChannelAuthorization() {
    const authorizationId = this.data.channelView?.authorizationId;
    const session = currentAuthSession();
    if (!authorizationId || !session || this.data.channelPolling || !this.data.channelSheetOpen) {
      return;
    }
    this.setData({ channelPolling: true, channelError: "" });
    try {
      const response = await new ILinkChannelApi(
        new ApiClient(undefined, session.accessToken),
      ).pollHubBot(authorizationId);
      const view = response.data;
      this.setData({
        channelView: view,
        channelStatus: "ready",
        channelPolling: false,
        channelError: "",
      });
      if (["waiting_scan", "scanned"].includes(view.state)) {
        scheduleChannelPoll(this, 400);
      }
    } catch (error) {
      this.setData({
        channelPolling: false,
        channelStatus: "error",
        channelError:
          error instanceof ApiClientError
            ? `${error.message}（请求 ${error.requestId}）`
            : "暂时没有读到微信确认结果，请再检查一次。",
      });
    }
  },

  async revokeChannelLink() {
    const session = currentAuthSession();
    if (!this.data.channelView || !session || this.data.channelWriting) return;
    const decision = await wx.showModal({
      title: "解除微信岸岸？",
      content: "解除后，当前专属 Bot 会停止工作；再次使用需要重新扫码连接。",
      confirmText: "确认解除",
      confirmColor: "#c94747",
    });
    if (!decision.confirm) return;
    stopChannelPolling(this);
    this.setData({ channelWriting: true, channelError: "" });
    try {
      const key = await createIdempotencyKey("ilink-revoke");
      const response = await new ILinkChannelApi(
        new ApiClient(undefined, session.accessToken),
      ).revokeHubBot(key);
      this.setData({
        channelView: response.data,
        channelStatus: "ready",
        channelWriting: false,
      });
    } catch (error) {
      this.setData({
        channelWriting: false,
        channelError:
          error instanceof ApiClientError
            ? `${error.message}（请求 ${error.requestId}）`
            : "解除连接没有完成。",
      });
    }
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

async function writeChannel(page: CaptureImportPage, replacePending: boolean) {
  const session = currentAuthSession();
  if (!session || page.data.channelWriting) return;
  page.setData({ channelWriting: true, channelError: "" });
  try {
    const key = await createIdempotencyKey("ilink-hub-authorize");
    const response = await new ILinkChannelApi(
      new ApiClient(undefined, session.accessToken),
    ).startHubBot(replacePending, key);
    page.setData({
      channelView: response.data,
      channelStatus: "ready",
      channelWriting: false,
    });
    scheduleChannelPoll(page);
  } catch (error) {
    page.setData({
      channelWriting: false,
      channelError:
        error instanceof ApiClientError
          ? `${error.message}（请求 ${error.requestId}）`
          : "微信二维码没有生成成功，请重新试一次。",
    });
  }
}

function stopChannelPolling(page: CaptureImportPage): void {
  const timer = channelPollTimers.get(page);
  if (timer !== undefined) {
    clearTimeout(timer);
    channelPollTimers.delete(page);
  }
}

function scheduleChannelPoll(page: CaptureImportPage, delay = 0): void {
  stopChannelPolling(page);
  if (!page.data.channelSheetOpen) return;
  const timer = setTimeout(() => {
    channelPollTimers.delete(page);
    void page.pollChannelAuthorization();
  }, delay);
  channelPollTimers.set(page, timer);
}
