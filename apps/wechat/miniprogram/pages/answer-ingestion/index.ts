import { currentAuthSession } from "../../auth/session";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import { answerIngestionUrl, routes } from "../../app/navigation/routes";
import {
  AnswerIngestionController,
  type AnswerIngestionState,
} from "../../features/external-learning/answer-controller";
import { requireExternalLearningGateway } from "../../features/external-learning/contracts";
import { externalLearningError } from "../../features/external-learning/error";
import { ExternalLearningPageRuntimeRegistry } from "../../features/external-learning/page-runtime";
import {
  chooseAnswerImage,
  ensureCameraPermission,
  requestCameraSetting,
} from "../../features/external-learning/platform";

interface AnswerIngestionPageData {
  state: AnswerIngestionState | null;
  configurationError: string;
}

interface AnswerIngestionPageCustom {
  retryLoad(): void;
  selectPacket(event: WechatMiniprogram.BaseEvent): void;
  chooseMode(event: WechatMiniprogram.BaseEvent): void;
  inputAnswerString(event: WechatMiniprogram.Input): void;
  chooseCameraAnswer(): Promise<void>;
  chooseAlbumAnswer(): Promise<void>;
  createCandidate(): void;
  inputCandidate(event: WechatMiniprogram.Input): void;
  requestConfirmation(): void;
  cancelConfirmation(): void;
  confirmAndCommit(): void;
  resetDraft(): void;
  openLearningPackets(): void;
  noop(): void;
}

type AnswerIngestionPage = WechatMiniprogram.Page.Instance<
  AnswerIngestionPageData,
  AnswerIngestionPageCustom
>;

const runtimes = new ExternalLearningPageRuntimeRegistry<
  AnswerIngestionPage,
  AnswerIngestionController
>();

Page<AnswerIngestionPageData, AnswerIngestionPageCustom>({
  data: {
    state: null,
    configurationError: "",
  },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      const returnTo = answerIngestionUrl(
        options.ingestionId
          ? { ingestionId: options.ingestionId }
          : options.packetVersionId
            ? { packetVersionId: options.packetVersionId }
            : {},
      );
      void wx.reLaunch({ url: loginUrlForReturn(returnTo) });
      return;
    }
    try {
      const gateway = requireExternalLearningGateway(session);
      const controller = new AnswerIngestionController({
        gateway,
        onChange: (state) => {
          if (runtimes.owns(this, controller)) this.setData({ state });
        },
      });
      runtimes.attach(this, controller);
      this.setData({ state: controller.snapshot(), configurationError: "" });
      const packetVersionId = options.packetVersionId?.trim();
      const ingestionId = options.ingestionId?.trim();
      if (ingestionId) {
        void controller.loadIngestion(ingestionId);
      } else {
        void controller.loadTargets().then(() => {
          if (packetVersionId && runtimes.owns(this, controller)) {
            controller.selectPacket(packetVersionId);
          }
        });
      }
    } catch (error) {
      this.setData({ configurationError: externalLearningError(error).message });
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  retryLoad() {
    const controller = runtimes.get(this);
    if (!controller) return;
    const state = controller.snapshot();
    if (state.pendingRecovery && !state.workspace) void controller.createCandidate();
    else void controller.loadTargets();
  },

  selectPacket(event) {
    runtimes.get(this)?.selectPacket(String(event.currentTarget.dataset.id ?? ""));
  },

  chooseMode(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "answer_string" || value === "answer_image") {
      runtimes.get(this)?.setInputMode(value);
    }
  },

  inputAnswerString(event) {
    runtimes.get(this)?.setAnswerText(event.detail.value);
  },

  async chooseCameraAnswer() {
    let granted = (await ensureCameraPermission()) === "granted";
    if (!granted) granted = await requestCameraSetting();
    if (!granted) return;
    await chooseAnswer(this, "camera");
  },

  async chooseAlbumAnswer() {
    await chooseAnswer(this, "album");
  },

  createCandidate() {
    void runtimes.get(this)?.createCandidate();
  },

  inputCandidate(event) {
    runtimes
      .get(this)
      ?.updateCandidate(String(event.currentTarget.dataset.id ?? ""), event.detail.value);
  },

  requestConfirmation() {
    runtimes.get(this)?.requestConfirmation();
  },

  cancelConfirmation() {
    runtimes.get(this)?.cancelConfirmation();
  },

  confirmAndCommit() {
    void runtimes.get(this)?.confirmAndCommit();
  },

  resetDraft() {
    runtimes.get(this)?.resetDraft();
  },

  openLearningPackets() {
    void wx.navigateTo({ url: routes.learningPackets });
  },

  noop() {},
});

async function chooseAnswer(page: AnswerIngestionPage, source: "camera" | "album") {
  try {
    const file = await chooseAnswerImage(source);
    const controller = runtimes.get(page);
    if (file && controller) {
      controller.setInputMode("answer_image");
      controller.setAnswerImage(file);
    }
  } catch (error) {
    void wx.showToast({ title: externalLearningError(error).message, icon: "none" });
  }
}
