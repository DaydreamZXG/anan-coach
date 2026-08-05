import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import {
  positionCompareUrl,
  positionDetailUrl,
  positionGoalUrl,
  routes,
} from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";
import { storeCompanionLaunchContext } from "../../../features/companion/context-store";
import {
  PositionDetailController,
  type PositionDetailState,
} from "../../../features/positions/detail-controller";
import {
  PositionPageRuntimeRegistry,
  type PositionController,
} from "../../../features/positions/runtime";
import { PositionStateStore } from "../../../features/positions/state-store";

interface PositionDetailPageData {
  state: PositionDetailState | null;
  scoreSheetOpen: boolean;
  comparisonSelected: boolean;
  comparisonCount: number;
}

interface PositionDetailPageCustom {
  goBack(): void;
  retry(): void;
  openQualificationProfile(): void;
  openPositionsWorkspace(): void;
  openPreparation(): void;
  confirmRequirement(event: WechatMiniprogram.BaseEvent): void;
  toggleComparison(): void;
  openComparison(): void;
  openScoreSheet(): void;
  closeScoreSheet(): void;
  openGoalChange(): void;
  askAnan(): void;
  noop(): void;
}

type PositionDetailPage = WechatMiniprogram.Page.Instance<
  PositionDetailPageData,
  PositionDetailPageCustom
>;

interface DetailPageController extends PositionController {
  detail: PositionDetailController;
  store: PositionStateStore;
  hasShown: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionDetailPage, DetailPageController>();

Page<PositionDetailPageData, PositionDetailPageCustom>({
  data: {
    state: null,
    scoreSheetOpen: false,
    comparisonSelected: false,
    comparisonCount: 0,
  },

  onLoad(options: Record<string, string | undefined>) {
    const positionId = options.positionId?.trim() ?? "";
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({
        url: positionId ? loginUrlForReturn(positionDetailUrl(positionId)) : routes.login,
      });
      return;
    }
    const runtime = runtimes.attach(this);
    const store = new PositionStateStore(session.userId);
    const controller = new PositionDetailController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      store,
      positionId,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ state });
      },
    });
    runtime.controller = {
      detail: controller,
      store,
      hasShown: false,
      dispose: () => {
        controller.dispose();
      },
    };
    const comparisonIds = store.loadComparison();
    this.setData({
      state: controller.snapshot(),
      comparisonSelected: comparisonIds.includes(positionId),
      comparisonCount: comparisonIds.length,
    });
    if (!positionId) {
      void wx.showToast({ title: "岗位信息不全，回去重选一个就好", icon: "none" });
      return;
    }
    void controller.load();
  },

  onShow() {
    const controller = runtimes.get(this)?.controller;
    if (!controller) return;
    if (!controller.hasShown) {
      controller.hasShown = true;
      return;
    }
    void controller.detail.load({ silent: true });
  },

  onUnload() {
    runtimes.detach(this);
  },

  goBack() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.redirectTo({ url: routes.positions });
  },

  retry() {
    void runtimes.get(this)?.controller?.detail.load();
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
  },

  openPositionsWorkspace() {
    void wx.redirectTo({ url: routes.positions });
  },

  openPreparation() {
    void wx.switchTab({ url: routes.ability });
  },

  confirmRequirement(event) {
    const requirementId = String(event.currentTarget.dataset.requirementId ?? "");
    const response = String(event.currentTarget.dataset.response ?? "");
    if (
      !requirementId ||
      (response !== "satisfied" && response !== "not_satisfied" && response !== "uncertain")
    ) {
      return;
    }
    void runtimes.get(this)?.controller?.detail.confirmRequirement(requirementId, response);
  },

  toggleComparison() {
    const runtime = runtimes.get(this)?.controller;
    const positionId = this.data.state?.positionId;
    if (!runtime || !positionId) return;
    const current = runtime.store.loadComparison();
    const selected = current.includes(positionId);
    if (!selected && current.length >= 3) {
      void wx.showToast({ title: "一次最多对比 3 个岗位", icon: "none" });
      return;
    }
    const next = selected
      ? current.filter((item) => item !== positionId)
      : [...current, positionId];
    runtime.store.saveComparison(next);
    this.setData({
      comparisonSelected: !selected,
      comparisonCount: next.length,
    });
  },

  openComparison() {
    const ids = runtimes.get(this)?.controller?.store.loadComparison() ?? [];
    if (ids.length < 2) return;
    void wx.navigateTo({ url: positionCompareUrl(ids) });
  },

  openScoreSheet() {
    this.setData({ scoreSheetOpen: true });
  },

  closeScoreSheet() {
    this.setData({ scoreSheetOpen: false });
  },

  openGoalChange() {
    const positionId = this.data.state?.positionId;
    if (positionId) void wx.navigateTo({ url: positionGoalUrl(positionId) });
  },

  askAnan() {
    const auth = currentAuthSession();
    const state = this.data.state;
    if (!auth || !state || state.status !== "ready" || !state.snapshotId || !state.summaryView) {
      return;
    }
    const fitSummary = state.fit
      ? `${state.fit.statusLabel}；${state.fit.scoreGapAvailable ? `可比分差 ${state.fit.scoreGapRangeLabel}` : state.fit.evidenceReasonLabel}`
      : "个人分差暂未形成";
    storeCompanionLaunchContext(auth.userId, {
      sourceLabel: "当前查看的岗位",
      title: `${state.summaryView.departmentFull} · ${state.summaryView.name}`,
      summary: `${state.summaryView.eligibilityLabel}；建议备考线 ${state.summaryView.scoreLabel}；${fitSummary}`,
      suggestedQuestion:
        "结合这个岗位的资格逐项核验、目标分依据和我的当前能力，告诉我它是否值得作为主目标，并指出还要确认的风险。",
      routeId: "pages/positions/detail/index",
      workspace: "position",
      objectRefs: [
        {
          type: "position",
          id: state.positionId,
          version: state.snapshotId,
        },
      ],
    });
    void wx.switchTab({ url: routes.anan });
  },

  noop() {},
});
