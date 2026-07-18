import { currentAuthSession } from "../../auth/session";
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

interface CaptureImportPageData {
  state: CaptureImportState | null;
  configurationError: string;
  mode: "search" | "import";
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
  retryImport(): void;
  restart(): void;
  openLearningPackets(): void;
  openAnswerIngestion(): void;
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
