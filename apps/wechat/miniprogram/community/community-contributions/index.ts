import { ApiClient } from "../../api/client";
import {
  CommunityMethodCardsApi,
  type CommunityCardState,
} from "../shared/api/community-method-cards";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  assertRewardLedgerTruth,
  mapCommunityError,
  type CommercialPageState,
} from "../shared/features/community-method-cards/contracts";
import {
  cardStateLabel,
  commercialStateView,
  presentVersion,
  type ContributionVersionView,
} from "../shared/features/community-method-cards/presentation";
import {
  CommunityPageRuntimeRegistry,
  type OwnedRuntime,
} from "../shared/features/community-method-cards/runtime";

interface ContributionCardView {
  cardId: string;
  state: CommunityCardState;
  stateLabel: string;
  reviewLabel: string;
  headVersionId: string;
  versions: ContributionVersionView[];
}

interface ContributionsPageData {
  pageState: CommercialPageState;
  stateTitle: string;
  stateDescription: string;
  stateActionLabel: string;
  requestId: string;
  cards: ContributionCardView[];
  activePolicyVersions: string[];
  policyMessage: string;
  aiQuotaBalance: number;
  membershipBalanceLabel: string;
  entitlementVersion: string;
  awardIds: string[];
  entryIds: string[];
  partialMessage: string;
}

interface ContributionsPageCustom {
  loadContributions(): Promise<void>;
  stateAction(): void;
  onPullDownRefresh(): Promise<void>;
}

interface ContributionsRuntime extends OwnedRuntime {
  api: CommunityMethodCardsApi;
  busy: boolean;
}

type ContributionsPage = WechatMiniprogram.Page.Instance<
  ContributionsPageData,
  ContributionsPageCustom
>;
const runtimes = new CommunityPageRuntimeRegistry<ContributionsPage, ContributionsRuntime>();

Page<ContributionsPageData, ContributionsPageCustom>({
  data: {
    pageState: "loading",
    stateTitle: commercialStateView("loading").title,
    stateDescription: commercialStateView("loading").description,
    stateActionLabel: "",
    requestId: "",
    cards: [],
    activePolicyVersions: [],
    policyMessage: "",
    aiQuotaBalance: 0,
    membershipBalanceLabel: "0分钟",
    entitlementVersion: "",
    awardIds: [],
    entryIds: [],
    partialMessage: "",
  },

  onLoad() {
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
      busy: false,
    });
    void this.loadContributions();
  },

  onShow() {
    const runtime = runtimes.get(this);
    if (!runtime) return;
    const session = currentAuthSession();
    if (!session || session.userId !== runtime.ownerUserId) {
      runtimes.detach(this);
      this.setData({
        cards: [],
        activePolicyVersions: [],
        aiQuotaBalance: 0,
        membershipBalanceLabel: "0分钟",
        entitlementVersion: "",
        awardIds: [],
        entryIds: [],
      });
      setState(this, "expired", "账号已经变化，原账号贡献与账本已从页面清除。");
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  async onPullDownRefresh() {
    await this.loadContributions();
    await wx.stopPullDownRefresh();
  },

  async loadContributions() {
    const runtime = runtimes.get(this);
    if (!runtime || runtime.busy) return;
    runtime.busy = true;
    const epoch = ++runtime.requestEpoch;
    setState(this, "loading");
    this.setData({ partialMessage: "" });
    try {
      const [contributions, ledger] = await Promise.all([
        runtime.api.getContributions(),
        runtime.api.getRewardLedger(),
      ]);
      if (!runtimes.owns(this, runtime, epoch)) return;
      assertRewardLedgerTruth(ledger);
      const results = await Promise.allSettled(
        contributions.cardIds.map((cardId) => runtime.api.getWorkspace(cardId)),
      );
      if (!runtimes.owns(this, runtime, epoch)) return;
      const cards = results.flatMap((result): ContributionCardView[] => {
        if (result.status === "rejected") return [];
        const workspace = result.value;
        return [
          {
            cardId: workspace.cardId,
            state: workspace.status,
            stateLabel: cardStateLabel(workspace.status),
            reviewLabel: reviewStatusLabel(workspace.reviewStatus),
            headVersionId: workspace.headVersionId,
            versions: workspace.versions.map(presentVersion).reverse(),
          },
        ];
      });
      const failedCount = results.length - cards.length;
      const policyMessage = ledger.activePolicyVersions.length
        ? `当前有 ${String(ledger.activePolicyVersions.length)} 项奖励规则生效。只有账户中的正式奖励记录才算发放。`
        : "当前没有已发布的奖励政策，因此不会授予任何额度或会员时长。";
      this.setData({
        cards,
        activePolicyVersions: ledger.activePolicyVersions,
        policyMessage,
        aiQuotaBalance: ledger.aiQuotaBalance,
        membershipBalanceLabel: formatMembershipSeconds(ledger.membershipSecondsBalance),
        entitlementVersion: ledger.entitlementVersion,
        awardIds: ledger.awardIds,
        entryIds: ledger.entryIds,
        partialMessage:
          failedCount > 0 ? `${String(failedCount)} 张方法卡的最新内容暂未读取成功。` : "",
      });
      if (failedCount > 0) {
        setState(this, "partial", "已读到的奖励与方法卡会保留；未载入卡片不会临时补造。");
      } else if (
        cards.length === 0 &&
        ledger.awardIds.length === 0 &&
        ledger.entryIds.length === 0
      ) {
        setState(
          this,
          "empty",
          "你还没有方法卡贡献或奖励流水。无政策时不会显示虚构奖励。",
          "",
          "还没有贡献记录",
        );
      } else {
        setState(this, "default");
      }
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
    else void this.loadContributions();
  },
});

function reviewStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    none: "尚未送审",
    pending: "五项检查中",
    passed: "五项检查通过",
    revision_required: "检查要求修改",
    rejected: "检查未通过",
  };
  return labels[value] ?? "按审核状态展示";
}

function formatMembershipSeconds(value: number): string {
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (hours > 0) return `${String(hours)}小时${minutes ? `${String(minutes)}分钟` : ""}`;
  return `${String(minutes)}分钟`;
}

function setState(
  page: ContributionsPage,
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
