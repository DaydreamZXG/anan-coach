import { currentAuthSession } from "../../auth/session";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { answerIngestionUrl, captureImportUrl, routes } from "../../app/navigation/routes";
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

interface CaptureImportPageData {
  state: CaptureImportState | null;
  configurationError: string;
}

interface CaptureImportPageCustom {
  chooseCamera(): Promise<void>;
  chooseAlbum(): Promise<void>;
  choosePdf(): Promise<void>;
  chooseTrack(event: WechatMiniprogram.BaseEvent): void;
  inputSegment(event: WechatMiniprogram.Input): void;
  selectMatch(event: WechatMiniprogram.BaseEvent): void;
  confirmImport(): void;
  retryImport(): void;
  openLearningPackets(): void;
  openAnswerIngestion(): void;
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
  },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: loginUrlForReturn(captureImportUrl(options.importId)) });
      return;
    }
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

  inputSegment(event) {
    runtimes
      .get(this)
      ?.updateSegment(String(event.currentTarget.dataset.id ?? ""), event.detail.value);
  },

  selectMatch(event) {
    runtimes.get(this)?.selectMatch(String(event.currentTarget.dataset.id ?? ""));
  },

  confirmImport() {
    void runtimes.get(this)?.confirm();
  },

  retryImport() {
    const controller = runtimes.get(this);
    if (!controller) return;
    if (controller.snapshot().pendingRecovery) void controller.retryPendingImport();
    else void controller.refresh();
  },

  openLearningPackets() {
    void wx.navigateTo({ url: routes.learningPackets });
  },

  openAnswerIngestion() {
    void wx.navigateTo({ url: answerIngestionUrl() });
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
