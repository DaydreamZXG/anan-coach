import { currentAuthSession } from "../../auth/session";
import { ApiClient, ApiClientError } from "../../api/client";
import { ILinkChannelApi, type ILinkChannelView } from "../../api/ilink-channel";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import {
  answerIngestionUrl,
  captureImportUrl,
  learningPacketsUrl,
} from "../../app/navigation/routes";
import {
  CaptureImportController,
  type CaptureImportState,
} from "../../features/external-learning/capture-controller";
import { requireExternalLearningGateway } from "../../features/external-learning/contracts";
import { externalLearningError } from "../../features/external-learning/error";
import { ExternalLearningPageRuntimeRegistry } from "../../features/external-learning/page-runtime";
import {
  chooseQuestionImage,
  chooseQuestionPdf,
  ensureCameraPermission,
  requestCameraSetting,
} from "../../features/external-learning/platform";
import { createIdempotencyKey } from "../../platform/command-id";

interface CaptureImportPageData {
  state: CaptureImportState | null;
  configurationError: string;
  mode: "search" | "import";
  channelView: ILinkChannelView | null;
  channelStatus: "loading" | "ready" | "unavailable" | "error";
  channelError: string;
  channelWriting: boolean;
  channelExpired: boolean;
}

interface CaptureImportPageCustom {
  chooseCamera(): Promise<void>;
  chooseAlbum(): Promise<void>;
  choosePdf(): Promise<void>;
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
  loadChannel(): Promise<void>;
  issueChannelCode(): Promise<void>;
  replaceChannelCode(): Promise<void>;
  copyChannelCode(): void;
  revokeChannelLink(): Promise<void>;
  goBack(): void;
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
    channelStatus: "loading",
    channelError: "",
    channelWriting: false,
    channelExpired: false,
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
          if (runtimes.owns(this, controller)) this.setData({ state });
        },
      });
      runtimes.attach(this, controller);
      this.setData({ state: controller.snapshot(), configurationError: "" });
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
    if (!granted) return;
    await chooseAndStart(this, "camera");
  },

  async chooseAlbum() {
    await chooseAndStart(this, "album");
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
  },

  openLearningPackets() {
    const contentVersionId =
      this.data.state?.activeQuestionUnit?.match?.confirmedContentVersionId?.trim() ?? "";
    if (!contentVersionId) {
      void wx.showToast({ title: "请先确认匹配的题目版本", icon: "none" });
      return;
    }
    void wx.navigateTo({ url: learningPacketsUrl({ contentVersionId }) });
  },

  openAnswerIngestion() {
    void wx.navigateTo({ url: answerIngestionUrl() });
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
          channelError: "微信发图搜题入口暂未开放；请先用下方小程序拍照搜题。",
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
    void wx.navigateBack();
  },
});

async function chooseAndStart(page: CaptureImportPage, source: "camera" | "album") {
  try {
    const file = await chooseQuestionImage(source);
    const controller = runtimes.get(page);
    if (file && controller) await controller.startImport(file);
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
