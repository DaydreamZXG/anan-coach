import { ApiClient } from "../../api/client";
import {
  CommunityMethodCardsApi,
  type CommunityCardWorkspace,
  type CommunityRightsDeclaration,
  type CommunityStructureCandidate,
} from "../shared/api/community-method-cards";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  assertStructureCandidate,
  buildConfirmation,
  mapCommunityError,
  rightsOptions,
  type CommercialPageState,
} from "../shared/features/community-method-cards/contracts";
import {
  cardStateLabel,
  commercialStateView,
} from "../shared/features/community-method-cards/presentation";
import {
  CommunityPageRuntimeRegistry,
  type OwnedRuntime,
} from "../shared/features/community-method-cards/runtime";
import { createIdempotencyKey } from "../../platform/command-id";

interface StructurePageData {
  pageState: CommercialPageState;
  stateTitle: string;
  stateDescription: string;
  stateActionLabel: string;
  requestId: string;
  cardId: string;
  cardStateLabel: string;
  title: string;
  recognition: string;
  methodStepsText: string;
  applicabilityText: string;
  exclusionsText: string;
  pitfallsText: string;
  rightsDeclaration: CommunityRightsDeclaration;
  rightsOptions: typeof rightsOptions;
  candidateNotice: string;
  formMessage: string;
  confirmedVersionId: string;
  reviewMessage: string;
  interactionLocked: boolean;
  canPublish: boolean;
}

interface StructurePageCustom {
  loadWorkspace(): Promise<void>;
  stateAction(): void;
  inputTitle(event: WechatMiniprogram.Input): void;
  inputRecognition(event: WechatMiniprogram.Input): void;
  inputSteps(event: WechatMiniprogram.Input): void;
  inputApplicability(event: WechatMiniprogram.Input): void;
  inputExclusions(event: WechatMiniprogram.Input): void;
  inputPitfalls(event: WechatMiniprogram.Input): void;
  chooseRights(event: WechatMiniprogram.BaseEvent): void;
  confirmStructure(): Promise<void>;
  submitReview(): Promise<void>;
}

interface StructureRuntime extends OwnedRuntime {
  api: CommunityMethodCardsApi;
  cardId: string;
  workspace: CommunityCardWorkspace | null;
  candidate: CommunityStructureCandidate | null;
  busy: boolean;
}

type StructurePage = WechatMiniprogram.Page.Instance<StructurePageData, StructurePageCustom>;
const runtimes = new CommunityPageRuntimeRegistry<StructurePage, StructureRuntime>();

Page<StructurePageData, StructurePageCustom>({
  data: {
    pageState: "loading",
    stateTitle: commercialStateView("loading").title,
    stateDescription: commercialStateView("loading").description,
    stateActionLabel: "",
    requestId: "",
    cardId: "",
    cardStateLabel: "",
    title: "",
    recognition: "",
    methodStepsText: "",
    applicabilityText: "",
    exclusionsText: "",
    pitfallsText: "",
    rightsDeclaration: "original",
    rightsOptions,
    candidateNotice: "",
    formMessage: "",
    confirmedVersionId: "",
    reviewMessage: "",
    interactionLocked: false,
    canPublish: true,
  },

  onLoad(options: Record<string, string | undefined>) {
    const cardId = options.cardId?.trim() ?? "";
    if (!cardId) {
      setState(this, "error", "缺少方法卡标识，不能编辑孤立候选。", "", "页面地址不完整");
      return;
    }
    const session = currentAuthSession();
    if (!session) {
      setState(this, "expired");
      return;
    }
    runtimes.attach(this, {
      api: new CommunityMethodCardsApi(new ApiClient(environment.apiBaseUrl, session.accessToken)),
      ownerUserId: session.userId,
      requestEpoch: 0,
      disposed: false,
      cardId,
      workspace: null,
      candidate: null,
      busy: false,
    });
    this.setData({ cardId });
    void this.loadWorkspace();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    const session = currentAuthSession();
    if (!session || session.userId !== runtime.ownerUserId) {
      runtimes.detach(this);
      clearSensitiveData(this);
      setState(this, "expired", "账号已经变化，原账号候选已从当前页面清除。");
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  async loadWorkspace() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.busy) return;
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    setState(this, "loading");
    this.setData({ interactionLocked: true, formMessage: "", reviewMessage: "" });
    try {
      const workspace = await runtime.api.getWorkspace(runtime.cardId);
      if (!runtimes.owns(this, runtime, epoch)) return;
      if (workspace.versions.length === 0) {
        runtime.workspace = workspace;
        setState(this, "empty", "没有找到可编辑的草稿。", "", "草稿为空");
        return;
      }
      runtime.workspace = workspace;
      this.setData({ cardStateLabel: cardStateLabel(workspace.status) });
      const candidate = assertStructureCandidate(
        await runtime.api.generateCandidate(runtime.cardId),
        workspace,
      );
      if (!runtimes.owns(this, runtime, epoch)) return;
      runtime.candidate = candidate;
      if (candidate.status === "processing") {
        setState(this, "processing", "岸岸正在整理候选结构；当前文字还不是事实，也不能送审。");
        return;
      }
      if (candidate.status === "expired") {
        setState(this, "conflict", "草稿刚刚有更新，请重新生成整理结果。");
        return;
      }
      if (candidate.status === "failed") {
        setState(this, "partial", "私有草稿已保存，但本次结构候选没有生成成功。");
        return;
      }
      this.setData({
        title: candidate.title,
        recognition: candidate.recognition,
        methodStepsText: candidate.methodSteps.join("\n"),
        applicabilityText: workspace.versions.at(-1)?.applicability.join("，") ?? "",
        exclusionsText: "",
        pitfallsText: "",
        candidateNotice: "AI 候选不是事实。请逐项编辑，确认后才算你认可。",
        interactionLocked: false,
        confirmedVersionId: "",
      });
      setState(this, "default");
    } catch (error) {
      if (!runtimes.owns(this, runtime, epoch)) return;
      const view = mapCommunityError(error);
      setState(this, view.state, view.message, view.requestId, view.title);
    } finally {
      if (runtimes.owns(this, runtime, epoch)) {
        runtime.busy = false;
        this.setData({ interactionLocked: false });
      }
    }
  },

  stateAction() {
    if (this.data.pageState === "expired") {
      void wx.reLaunch({ url: routes.login });
    } else {
      void this.loadWorkspace();
    }
  },

  inputTitle(event) {
    this.setData({ title: event.detail.value, formMessage: "", confirmedVersionId: "" });
  },
  inputRecognition(event) {
    this.setData({ recognition: event.detail.value, formMessage: "", confirmedVersionId: "" });
  },
  inputSteps(event) {
    this.setData({ methodStepsText: event.detail.value, formMessage: "", confirmedVersionId: "" });
  },
  inputApplicability(event) {
    this.setData({
      applicabilityText: event.detail.value,
      formMessage: "",
      confirmedVersionId: "",
    });
  },
  inputExclusions(event) {
    this.setData({ exclusionsText: event.detail.value, formMessage: "", confirmedVersionId: "" });
  },
  inputPitfalls(event) {
    this.setData({ pitfallsText: event.detail.value, formMessage: "", confirmedVersionId: "" });
  },

  chooseRights(event) {
    const value = String(event.currentTarget.dataset.value ?? "");
    const option = rightsOptions.find((item) => item.value === value);
    if (!option) return;
    this.setData({
      rightsDeclaration: option.value,
      canPublish: option.publishable,
      confirmedVersionId: "",
      formMessage: option.publishable ? "" : "第三方私有内容可以保留，但不能申请公开检查。",
    });
  },

  async confirmStructure() {
    const runtime = runtimes.get(this);
    if (!runtime?.workspace || !runtime.candidate || runtime.busy) return;
    let command;
    try {
      command = buildConfirmation(runtime.workspace, runtime.candidate, this.data);
    } catch (error) {
      this.setData({ formMessage: error instanceof Error ? error.message : "候选结构不完整。" });
      return;
    }
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ interactionLocked: true, formMessage: "" });
    setState(this, "processing", "正在保存你的确认。");
    try {
      const receipt = await runtime.api.confirmCandidate(command);
      if (!runtimes.owns(this, runtime, epoch)) return;
      this.setData({
        confirmedVersionId: receipt.versionId,
        formMessage: "",
        candidateNotice: `第 ${String(receipt.versionNumber)} 次确认已保存。后续编辑会另存一份，不覆盖这次试用结果。`,
      });
      setState(this, "default");
    } catch (error) {
      if (!runtimes.owns(this, runtime, epoch)) return;
      const view = mapCommunityError(error);
      setState(this, view.state, view.message, view.requestId, view.title);
    } finally {
      if (runtimes.owns(this, runtime, epoch)) {
        runtime.busy = false;
        this.setData({ interactionLocked: false });
      }
    }
  },

  async submitReview() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.busy || !this.data.confirmedVersionId) return;
    if (!this.data.canPublish) {
      this.setData({ formMessage: "当前权利声明只允许私有保存，不能公开送审。" });
      return;
    }
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ interactionLocked: true, formMessage: "" });
    setState(this, "processing", "正在执行重复、版权、安全、知识与答案一致性五项检查。");
    try {
      const idempotencyKey = await createIdempotencyKey("community-review");
      const result = await runtime.api.submitForReview(
        runtime.cardId,
        this.data.confirmedVersionId,
        idempotencyKey,
      );
      if (!runtimes.owns(this, runtime, epoch)) return;
      this.setData({
        reviewMessage:
          result.status === "trial"
            ? "五项检查已通过，这次确认的内容进入独立试用。"
            : result.status === "revision_required"
              ? "检查要求修改。原结论会保留，请根据意见另存一份。"
              : "这次确认的内容未通过检查，不会进入社区试用。",
        cardStateLabel: cardStateLabel(result.status),
      });
      setState(this, "default");
    } catch (error) {
      if (!runtimes.owns(this, runtime, epoch)) return;
      const view = mapCommunityError(error);
      setState(this, view.state, view.message, view.requestId, view.title);
    } finally {
      if (runtimes.owns(this, runtime, epoch)) {
        runtime.busy = false;
        this.setData({ interactionLocked: false });
      }
    }
  },
});

function setState(
  page: StructurePage,
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

function clearSensitiveData(page: StructurePage): void {
  page.setData({
    title: "",
    recognition: "",
    methodStepsText: "",
    applicabilityText: "",
    exclusionsText: "",
    pitfallsText: "",
    confirmedVersionId: "",
  });
}
