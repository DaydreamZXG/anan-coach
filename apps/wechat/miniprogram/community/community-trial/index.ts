import { ApiClient } from "../../api/client";
import {
  CommunityMethodCardsApi,
  type CommunityExposureReceipt,
  type CommunityFeedbackReason,
  type CommunityFeedbackVote,
  type CommunityReportCategory,
  type CommunityTrack,
  type CommunityTrialReceipt,
} from "../shared/api/community-method-cards";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  assertEligibleTrial,
  assertFeedbackChoice,
  assertTrialCard,
  feedbackReasons,
  mapCommunityError,
  reportCategories,
  splitTags,
  type CommercialPageState,
} from "../shared/features/community-method-cards/contracts";
import {
  commercialStateView,
  presentTrialCard,
  type TrialCardView,
} from "../shared/features/community-method-cards/presentation";
import {
  CommunityPageRuntimeRegistry,
  type OwnedRuntime,
} from "../shared/features/community-method-cards/runtime";

interface TrialAttemptReference {
  questionAttemptId: string;
  questionVersionId: string;
}

interface TrialPageData {
  pageState: CommercialPageState;
  stateTitle: string;
  stateDescription: string;
  stateActionLabel: string;
  requestId: string;
  cards: TrialCardView[];
  selectedCard: TrialCardView | null;
  activeIndex: number;
  exposureId: string;
  trialId: string;
  feedbackEligible: boolean;
  feedbackRecorded: boolean;
  feedbackVote: CommunityFeedbackVote;
  feedbackReason: CommunityFeedbackReason;
  feedbackReasons: typeof feedbackReasons;
  activeFeedbackReasons: typeof feedbackReasons;
  reportCategory: CommunityReportCategory;
  reportCategories: typeof reportCategories;
  reportMessage: string;
  trialMessage: string;
  evidenceReady: boolean;
  interactionLocked: boolean;
}

interface TrialPageCustom {
  loadCards(): Promise<void>;
  stateAction(): void;
  selectCard(event: WechatMiniprogram.BaseEvent): void;
  optIn(): Promise<void>;
  completeTrial(): Promise<void>;
  chooseVote(event: WechatMiniprogram.BaseEvent): void;
  chooseFeedbackReason(event: WechatMiniprogram.BaseEvent): void;
  submitFeedback(): Promise<void>;
  chooseReportCategory(event: WechatMiniprogram.BaseEvent): void;
  submitReport(): Promise<void>;
}

interface TrialRuntime extends OwnedRuntime {
  api: CommunityMethodCardsApi;
  track: CommunityTrack;
  applicability: string[];
  rankingPolicyVersion: string;
  exposure: CommunityExposureReceipt | null;
  trial: CommunityTrialReceipt | null;
  attemptReference: TrialAttemptReference | null;
  busy: boolean;
}

type TrialPage = WechatMiniprogram.Page.Instance<TrialPageData, TrialPageCustom>;
const runtimes = new CommunityPageRuntimeRegistry<TrialPage, TrialRuntime>();

Page<TrialPageData, TrialPageCustom>({
  data: {
    pageState: "loading",
    stateTitle: commercialStateView("loading").title,
    stateDescription: commercialStateView("loading").description,
    stateActionLabel: "",
    requestId: "",
    cards: [],
    selectedCard: null,
    activeIndex: 0,
    exposureId: "",
    trialId: "",
    feedbackEligible: false,
    feedbackRecorded: false,
    feedbackVote: "helpful",
    feedbackReason: "clear",
    feedbackReasons,
    activeFeedbackReasons: feedbackReasons.filter((reason) => reason.votes.includes("helpful")),
    reportCategory: "incorrect",
    reportCategories,
    reportMessage: "",
    trialMessage: "",
    evidenceReady: false,
    interactionLocked: false,
  },

  onLoad(options: Record<string, string | undefined>) {
    const session = currentAuthSession();
    if (!session) {
      setState(this, "expired");
      return;
    }
    const track: CommunityTrack = options.track === "essay" ? "essay" : "xingce";
    const applicability = splitTags(options.applicability ?? "");
    const attemptReference = parseAttemptReference(options);
    runtimes.attach(this, {
      api: new CommunityMethodCardsApi(new ApiClient(environment.apiBaseUrl, session.accessToken)),
      ownerUserId: session.userId,
      requestEpoch: 0,
      disposed: false,
      track,
      applicability,
      rankingPolicyVersion: "",
      exposure: null,
      trial: null,
      attemptReference,
      busy: false,
    });
    this.setData({ evidenceReady: attemptReference !== null });
    void this.loadCards();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    const session = currentAuthSession();
    if (!session || session.userId !== runtime.ownerUserId) {
      runtimes.detach(this);
      this.setData({ cards: [], selectedCard: null, exposureId: "", trialId: "" });
      setState(this, "expired", "账号已经变化，原账号的曝光与试用资格已从页面清除。");
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  async loadCards() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.busy) return;
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    setState(this, "loading");
    try {
      const response = await runtime.api.discover(runtime.track, runtime.applicability);
      if (!runtimes.owns(this, runtime, epoch)) return;
      runtime.rankingPolicyVersion = response.rankingPolicyVersion;
      response.cards.forEach(assertTrialCard);
      const cards = response.cards.map(presentTrialCard);
      this.setData({
        cards,
        selectedCard: cards[0] ?? null,
        activeIndex: 0,
        exposureId: "",
        trialId: "",
        feedbackEligible: false,
        feedbackRecorded: false,
        trialMessage: "",
      });
      setState(
        this,
        cards.length ? "default" : "empty",
        cards.length ? undefined : "当前适用范围还没有可试用的方法。",
      );
    } catch (error) {
      if (!runtimes.owns(this, runtime, epoch)) return;
      const view = mapCommunityError(error);
      setState(this, view.state, view.message, view.requestId, view.title);
    } finally {
      if (runtimes.owns(this, runtime, epoch)) runtime.busy = false;
    }
  },

  stateAction() {
    if (this.data.pageState === "expired") void wx.reLaunch({ url: routes.login });
    else void this.loadCards();
  },

  selectCard(event) {
    const index = Number(event.currentTarget.dataset.index);
    const card = this.data.cards[index];
    const runtime = runtimes.get(this);
    if (!card || !runtime || runtime.busy) return;
    runtime.exposure = null;
    runtime.trial = null;
    this.setData({
      activeIndex: index,
      selectedCard: card,
      exposureId: "",
      trialId: "",
      feedbackEligible: false,
      feedbackRecorded: false,
      trialMessage: "",
      reportMessage: "",
    });
  },

  async optIn() {
    const runtime = runtimes.get(this);
    const card = this.data.selectedCard;
    if (!runtime || !card || runtime.busy || !card.canTrial) return;
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ interactionLocked: true, trialMessage: "" });
    setState(this, "processing", "正在记录你主动加入试用；不会自动加入今日任务。");
    try {
      const exposure = await runtime.api.optInToTrial(
        card.cardVersionId,
        runtime.applicability,
        runtime.rankingPolicyVersion,
      );
      if (!runtimes.owns(this, runtime, epoch)) return;
      runtime.exposure = exposure;
      this.setData({
        exposureId: exposure.exposureId,
        trialMessage: runtime.attemptReference
          ? "已主动加入试用。系统会根据当前试题尝试回查独立新题结果。"
          : "已主动加入试用，但当前没有可回查的试题尝试，暂时不能评价。",
      });
      setState(
        this,
        runtime.attemptReference ? "default" : "partial",
        runtime.attemptReference
          ? undefined
          : "方法卡已曝光，但独立新题证据尚未就绪。完成真实试题后再回来。",
      );
      if (!runtime.attemptReference) this.setData({ stateActionLabel: "" });
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

  async completeTrial() {
    const runtime = runtimes.get(this);
    if (!runtime?.exposure || !runtime.attemptReference || runtime.busy) return;
    const command = {
      exposureId: runtime.exposure.exposureId,
      questionAttemptId: runtime.attemptReference.questionAttemptId,
      questionVersionId: runtime.attemptReference.questionVersionId,
    };
    try {
      assertEligibleTrial(command);
    } catch (error) {
      this.setData({ trialMessage: error instanceof Error ? error.message : "试题证据无效。" });
      return;
    }
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ interactionLocked: true });
    setState(this, "processing", "正在核对方法卡曝光与独立试题。");
    try {
      const trial = await runtime.api.completeTrial(command);
      if (!runtimes.owns(this, runtime, epoch)) return;
      runtime.trial = trial;
      this.setData({
        trialId: trial.trialId,
        feedbackEligible: trial.feedbackEligible,
        trialMessage: trial.feedbackEligible
          ? "独立新题结果已核对，现在可以提交一次固定反馈。"
          : "本次记录不满足反馈条件，不会计入效果判断。",
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

  chooseVote(event) {
    const vote = String(event.currentTarget.dataset.value ?? "");
    if (vote !== "helpful" && vote !== "not_helpful") return;
    const fallbackReason = vote === "helpful" ? "clear" : "too_specific";
    this.setData({
      feedbackVote: vote,
      feedbackReason: fallbackReason,
      activeFeedbackReasons: feedbackReasons.filter((reason) => reason.votes.includes(vote)),
    });
  },

  chooseFeedbackReason(event) {
    const reason = String(event.currentTarget.dataset.value ?? "") as CommunityFeedbackReason;
    const option = feedbackReasons.find((item) => item.value === reason);
    if (option?.votes.includes(this.data.feedbackVote)) this.setData({ feedbackReason: reason });
  },

  async submitFeedback() {
    const runtime = runtimes.get(this);
    if (
      !runtime?.trial ||
      !this.data.feedbackEligible ||
      runtime.busy ||
      this.data.feedbackRecorded
    )
      return;
    try {
      assertFeedbackChoice(this.data.feedbackVote, this.data.feedbackReason);
    } catch (error) {
      this.setData({ trialMessage: error instanceof Error ? error.message : "反馈选择无效。" });
      return;
    }
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ interactionLocked: true });
    setState(this, "processing", "正在记录这次固定反馈。");
    try {
      const receipt = await runtime.api.recordFeedback(
        runtime.trial.trialId,
        this.data.feedbackVote,
        this.data.feedbackReason,
      );
      if (!runtimes.owns(this, runtime, epoch)) return;
      this.setData({
        feedbackRecorded: receipt.recorded,
        trialMessage: receipt.recorded
          ? "反馈已记录。每位用户对同一份方法只保留一次有效反馈。"
          : "反馈未记录。",
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

  chooseReportCategory(event) {
    const value = String(event.currentTarget.dataset.value ?? "") as CommunityReportCategory;
    if (reportCategories.some((item) => item.value === value))
      this.setData({ reportCategory: value });
  },

  async submitReport() {
    const runtime = runtimes.get(this);
    const card = this.data.selectedCard;
    if (!runtime || !card || runtime.busy || (!runtime.exposure && !runtime.trial)) return;
    const evidenceRef = runtime.trial
      ? `community-trial:${runtime.trial.trialId}`
      : `community-exposure:${runtime.exposure?.exposureId ?? ""}`;
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    this.setData({ interactionLocked: true, reportMessage: "" });
    setState(this, "processing", "正在提交固定分类与已有证据引用。");
    try {
      const receipt = await runtime.api.report(
        card.cardVersionId,
        this.data.reportCategory,
        evidenceRef,
      );
      if (!runtimes.owns(this, runtime, epoch)) return;
      this.setData({ reportMessage: `举报已受理（${receipt.reportId}），状态由审核结果决定。` });
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

function parseAttemptReference(
  options: Record<string, string | undefined>,
): TrialAttemptReference | null {
  const questionAttemptId = options.questionAttemptId?.trim() ?? "";
  const questionVersionId = options.questionVersionId?.trim() ?? "";
  if (!questionAttemptId || !questionVersionId) return null;
  return { questionAttemptId, questionVersionId };
}

function setState(
  page: TrialPage,
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
