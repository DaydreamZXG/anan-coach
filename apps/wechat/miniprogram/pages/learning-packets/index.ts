import { currentAuthSession } from "../../auth/session";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { answerIngestionUrl, routes } from "../../app/navigation/routes";
import { requireExternalLearningGateway } from "../../features/external-learning/contracts";
import { externalLearningError } from "../../features/external-learning/error";
import { ExternalLearningPageRuntimeRegistry } from "../../features/external-learning/page-runtime";
import {
  LearningPacketsController,
  type LearningPacketsState,
} from "../../features/external-learning/packets-controller";
import { openPacketFile } from "../../features/external-learning/platform";

interface LearningPacketsPageData {
  state: LearningPacketsState | null;
  configurationError: string;
}

interface LearningPacketsPageCustom {
  retryLoad(): void;
  chooseTrack(event: WechatMiniprogram.BaseEvent): void;
  chooseFormat(event: WechatMiniprogram.BaseEvent): void;
  toggleContent(event: WechatMiniprogram.BaseEvent): void;
  createPacket(): void;
  openPacket(event: WechatMiniprogram.BaseEvent): void;
  retryPacket(event: WechatMiniprogram.BaseEvent): void;
  renewPacket(event: WechatMiniprogram.BaseEvent): void;
  openCaptureImport(): void;
  openAnswerIngestion(event: WechatMiniprogram.BaseEvent): void;
}

type LearningPacketsPage = WechatMiniprogram.Page.Instance<
  LearningPacketsPageData,
  LearningPacketsPageCustom
>;

const runtimes = new ExternalLearningPageRuntimeRegistry<
  LearningPacketsPage,
  LearningPacketsController
>();
const shownPages = new WeakSet<LearningPacketsPage>();

Page<LearningPacketsPageData, LearningPacketsPageCustom>({
  data: {
    state: null,
    configurationError: "",
  },

  onLoad() {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: loginUrlForReturn(routes.learningPackets) });
      return;
    }
    try {
      const gateway = requireExternalLearningGateway(session);
      const controller = new LearningPacketsController({
        gateway,
        onOpenFile: openPacketFile,
        onChange: (state) => {
          if (runtimes.owns(this, controller)) this.setData({ state });
        },
      });
      runtimes.attach(this, controller);
      this.setData({ state: controller.snapshot(), configurationError: "" });
      void controller.load();
    } catch (error) {
      this.setData({ configurationError: externalLearningError(error).message });
    }
  },

  onShow() {
    if (!shownPages.has(this)) {
      shownPages.add(this);
      return;
    }
    void runtimes.get(this)?.load();
  },

  onUnload() {
    runtimes.detach(this);
  },

  retryLoad() {
    const controller = runtimes.get(this);
    if (!controller) return;
    if (controller.snapshot().pendingRecovery) void controller.create();
    else void controller.load();
  },

  chooseTrack(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "xingce" || value === "essay") runtimes.get(this)?.setTrack(value);
  },

  chooseFormat(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "pdf" || value === "note_app_file") {
      runtimes.get(this)?.setRenderFormat(value);
    }
  },

  toggleContent(event) {
    runtimes.get(this)?.toggleContent(String(event.currentTarget.dataset.id ?? ""));
  },

  createPacket() {
    void runtimes.get(this)?.create();
  },

  openPacket(event) {
    void runtimes.get(this)?.open(String(event.currentTarget.dataset.id ?? ""));
  },

  retryPacket(event) {
    void runtimes.get(this)?.operate(String(event.currentTarget.dataset.id ?? ""), "retry");
  },

  renewPacket(event) {
    void runtimes.get(this)?.operate(String(event.currentTarget.dataset.id ?? ""), "renew");
  },

  openCaptureImport() {
    void wx.navigateTo({ url: routes.captureImport });
  },

  openAnswerIngestion(event) {
    const packetVersionId = String(event.currentTarget.dataset.id ?? "");
    void wx.navigateTo({
      url: answerIngestionUrl(packetVersionId ? { packetVersionId } : {}),
    });
  },
});
