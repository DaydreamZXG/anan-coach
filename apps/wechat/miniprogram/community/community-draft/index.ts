import { ApiClient } from "../../api/client";
import {
  CommunityMethodCardsApi,
  type CommunitySourceType,
  type CommunityTrack,
} from "../shared/api/community-method-cards";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  communitySourceOptions,
  mapCommunityError,
  splitTags,
  type CommercialPageState,
} from "../shared/features/community-method-cards/contracts";
import { commercialStateView } from "../shared/features/community-method-cards/presentation";
import {
  CommunityPageRuntimeRegistry,
  type OwnedRuntime,
} from "../shared/features/community-method-cards/runtime";
import { createIdempotencyKey } from "../../platform/command-id";

interface DraftPageData {
  pageState: CommercialPageState;
  stateTitle: string;
  stateDescription: string;
  stateActionLabel: string;
  requestId: string;
  sourceType: CommunitySourceType;
  sourceOptions: typeof communitySourceOptions;
  track: CommunityTrack;
  sourceText: string;
  applicabilityText: string;
  sourceArtifactBound: boolean;
  formMessage: string;
  submitting: boolean;
}

interface DraftPageCustom {
  chooseSource(event: WechatMiniprogram.BaseEvent): void;
  chooseTrack(event: WechatMiniprogram.BaseEvent): void;
  inputSource(event: WechatMiniprogram.Input): void;
  inputApplicability(event: WechatMiniprogram.Input): void;
  submitDraft(): Promise<void>;
  stateAction(): void;
}

interface DraftRuntime extends OwnedRuntime {
  api: CommunityMethodCardsApi;
  submitting: boolean;
  sourceArtifactId: string | null;
  reflectionVersionId: string | null;
}

type DraftPage = WechatMiniprogram.Page.Instance<DraftPageData, DraftPageCustom>;
const runtimes = new CommunityPageRuntimeRegistry<DraftPage, DraftRuntime>();

Page<DraftPageData, DraftPageCustom>({
  data: {
    pageState: "default",
    stateTitle: "",
    stateDescription: "",
    stateActionLabel: "",
    requestId: "",
    sourceType: "text",
    sourceOptions: communitySourceOptions,
    track: "xingce",
    sourceText: "",
    applicabilityText: "",
    sourceArtifactBound: false,
    formMessage: "",
    submitting: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      setState(this, "expired");
      return;
    }
    const requestedSourceType = options.sourceType?.trim() ?? "";
    const sourceType = communitySourceOptions.some((option) => option.value === requestedSourceType)
      ? (requestedSourceType as CommunitySourceType)
      : "text";
    const sourceArtifactId = options.sourceArtifactId?.trim() || null;
    const reflectionVersionId = options.reflectionVersionId?.trim() || null;
    runtimes.attach(this, {
      api: new CommunityMethodCardsApi(new ApiClient(environment.apiBaseUrl, session.accessToken)),
      ownerUserId: session.userId,
      requestEpoch: 0,
      disposed: false,
      submitting: false,
      sourceArtifactId,
      reflectionVersionId,
    });
    this.setData({
      sourceType,
      sourceArtifactBound: sourceArtifactId !== null,
    });
    setState(this, "default");
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    const session = currentAuthSession();
    if (!session || session.userId !== runtime.ownerUserId) {
      runtimes.detach(this);
      this.setData({ sourceText: "", applicabilityText: "" });
      setState(this, "expired", "账号已经变化，原账号草稿已从当前页面清除。");
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  chooseSource(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (communitySourceOptions.some((option) => option.value === value)) {
      this.setData({ sourceType: value as CommunitySourceType, formMessage: "" });
    }
  },

  chooseTrack(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    if (value === "xingce" || value === "essay") {
      this.setData({ track: value, formMessage: "" });
    }
  },

  inputSource(event) {
    this.setData({ sourceText: event.detail.value, formMessage: "" });
  },

  inputApplicability(event) {
    this.setData({ applicabilityText: event.detail.value, formMessage: "" });
  },

  async submitDraft() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.submitting) return;
    const sourceText = this.data.sourceText.trim();
    const applicability = splitTags(this.data.applicabilityText);
    if (!sourceText) {
      this.setData({ formMessage: "先写下你自己的方法表达，再让岸岸帮你整理。" });
      return;
    }
    if (applicability.length === 0) {
      this.setData({ formMessage: "至少说明一个适用题型或场景。" });
      return;
    }
    const artifactRequired =
      this.data.sourceType === "voice_transcript" ||
      this.data.sourceType === "handwriting_extraction";
    if (artifactRequired && !runtime.sourceArtifactId) {
      this.setData({
        formMessage: "语音转写或手写识别必须来自已经完成安全检查的素材，不能直接填写成识别结果。",
      });
      return;
    }
    const reflectionRequired =
      this.data.sourceType === "voice_transcript" || this.data.sourceType === "reflection";
    if (reflectionRequired && !runtime.reflectionVersionId) {
      this.setData({
        formMessage: "语音转写或错题复盘必须绑定已确认的复盘记录。",
      });
      return;
    }
    runtime.submitting = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ submitting: true, formMessage: "" });
    setState(this, "processing", "正在保存私有草稿并生成结构候选。");
    try {
      const idempotencyKey = await createIdempotencyKey("community-draft");
      const receipt = await runtime.api.createDraft({
        sourceType: this.data.sourceType,
        sourceText,
        track: this.data.track,
        applicability,
        ...(runtime.sourceArtifactId && this.data.sourceType !== "text"
          ? { sourceArtifactId: runtime.sourceArtifactId }
          : {}),
        ...(runtime.reflectionVersionId && reflectionRequired
          ? { reflectionVersionId: runtime.reflectionVersionId }
          : {}),
        idempotencyKey,
      });
      if (!runtimes.owns(this, runtime, epoch)) return;
      await wx.navigateTo({
        url: `/community/community-structure/index?cardId=${encodeURIComponent(receipt.cardId)}`,
      });
      if (runtimes.owns(this, runtime, epoch)) setState(this, "default");
    } catch (error) {
      if (!runtimes.owns(this, runtime, epoch)) return;
      const view = mapCommunityError(error);
      setState(this, view.state, view.message, view.requestId, view.title);
    } finally {
      if (runtimes.owns(this, runtime, epoch)) {
        runtime.submitting = false;
        this.setData({ submitting: false });
      }
    }
  },

  stateAction() {
    if (this.data.pageState === "expired") {
      void wx.reLaunch({ url: routes.login });
      return;
    }
    if (["error", "conflict", "recovery", "partial"].includes(this.data.pageState)) {
      setState(this, "default");
    }
  },
});

function setState(
  page: DraftPage,
  state: CommercialPageState,
  description?: string,
  requestId = "",
  title?: string,
): void {
  const view = commercialStateView(state);
  page.setData({
    pageState: state,
    stateTitle: title ?? view.title,
    stateDescription: description ?? view.description,
    stateActionLabel: view.actionLabel,
    requestId,
  });
}
