import { currentAuthSession } from "../../auth/session";
import { ApiClient, ApiClientError } from "../../api/client";
import { ILinkChannelApi, type ILinkChannelView } from "../../api/ilink-channel";
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
} from "../../features/external-learning/platform";
import type { LocalImportFile } from "../../features/external-learning/contracts";
import { createIdempotencyKey } from "../../platform/command-id";

interface CaptureImportPageData {
  state: CaptureImportState | null;
  configurationError: string;
  mode: "search" | "import";
  channelView: ILinkChannelView | null;
  channelStatus: "hidden" | "loading" | "ready" | "unavailable" | "error";
  channelError: string;
  channelWriting: boolean;
  channelExpired: boolean;
  captureStage: "camera" | "crop";
  permissionDenied: boolean;
  pendingFile: LocalImportFile | null;
  recordSheetOpen: boolean;
  receiptVisible: boolean;
  channelSheetOpen: boolean;
  matchCandidates: MatchCandidateView[];
}

interface CaptureImportPageCustom {
  chooseCamera(): Promise<void>;
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
  issueChannelCode(): Promise<void>;
  replaceChannelCode(): Promise<void>;
  copyChannelCode(): void;
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

Page<CaptureImportPageData, CaptureImportPageCustom>({
  data: {
    state: null,
    configurationError: "",
    mode: "search",
    channelView: null,
    channelStatus: "hidden",
    channelError: "",
    channelWriting: false,
    channelExpired: false,
    captureStage: "camera",
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
    runtimes.detach(this);
  },

  async chooseCamera() {
    let granted = (await ensureCameraPermission()) === "granted";
    if (!granted) granted = await requestCameraSetting();
    if (!granted) {
      this.setData({ permissionDenied: true });
      return;
    }
    this.setData({ permissionDenied: false });
    await chooseForReview(this, "camera");
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
    this.setData({ captureStage: "camera", pendingFile: null });
  },

  async retryCameraPermission() {
    const granted = await requestCameraSetting();
    this.setData({ permissionDenied: !granted });
    if (granted) await chooseForReview(this, "camera");
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
    const state = this.data.state;
    const workspace = state?.workspace;
    const unit = state?.activeQuestionUnit;
    if (!session || !state || !workspace || !unit) return;
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

  async loadChannel() {
    const session = currentAuthSession();
    if (!session) return;
    this.setData({ channelStatus: "loading", channelError: "" });
    try {
      const response = await new ILinkChannelApi(
        new ApiClient(undefined, session.accessToken),
      ).get();
      const view = response.data;
      this.setData({
        channelView: view,
        channelStatus: "ready",
        channelError: "",
        channelExpired:
          view.linkState === "pending" &&
          Boolean(view.expiresAt) &&
          new Date(view.expiresAt ?? 0).getTime() <= Date.now(),
      });
    } catch (error) {
      if (error instanceof ApiClientError && error.statusCode === 503) {
        this.setData({
          channelStatus: "unavailable",
          channelError: "",
          channelSheetOpen: false,
        });
        return;
      }
      this.setData({
        channelStatus: "error",
        channelError:
          error instanceof ApiClientError
            ? `${error.message}（请求 ${error.requestId}）`
            : "微信发图搜题连接状态没有加载成功。",
      });
    }
  },

  async issueChannelCode() {
    await writeChannel(this, false);
  },

  async replaceChannelCode() {
    await writeChannel(this, true);
  },

  copyChannelCode() {
    const code = this.data.channelView?.bindingCode?.trim();
    if (!code || this.data.channelExpired) return;
    void wx.setClipboardData({ data: code });
  },

  async revokeChannelLink() {
    const linkId = this.data.channelView?.linkId;
    const session = currentAuthSession();
    if (!linkId || !session || this.data.channelWriting) return;
    const decision = await wx.showModal({
      title: "解除微信发图搜题？",
      content: "解除后，这个微信入口不能再读取你的查询和个人收录。历史内容仍可在账户内删除。",
      confirmText: "确认解除",
      confirmColor: "#c94747",
    });
    if (!decision.confirm) return;
    this.setData({ channelWriting: true, channelError: "" });
    try {
      const key = await createIdempotencyKey("ilink-revoke");
      const response = await new ILinkChannelApi(
        new ApiClient(undefined, session.accessToken),
      ).revoke(linkId, key);
      this.setData({
        channelView: response.data,
        channelStatus: "ready",
        channelWriting: false,
        channelExpired: false,
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
    void wx.navigateBack();
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

async function writeChannel(page: CaptureImportPage, replacePending: boolean) {
  const session = currentAuthSession();
  if (!session || page.data.channelWriting) return;
  page.setData({ channelWriting: true, channelError: "" });
  try {
    const key = await createIdempotencyKey("ilink-code");
    const response = await new ILinkChannelApi(new ApiClient(undefined, session.accessToken)).issue(
      replacePending,
      key,
    );
    page.setData({
      channelView: response.data,
      channelStatus: "ready",
      channelWriting: false,
      channelExpired: false,
    });
  } catch (error) {
    page.setData({
      channelWriting: false,
      channelError:
        error instanceof ApiClientError
          ? `${error.message}（请求 ${error.requestId}）`
          : "连接码没有生成成功。",
    });
  }
}
