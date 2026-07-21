import { ApiClient } from "../../api/client";
import { AbilityApi } from "../../api/ability";
import { QualificationApi } from "../../api/qualification";
import { loginUrlForReturn } from "../../app/navigation/auth-return";
import {
  positionCompareUrl,
  positionDetailUrl,
  positionSearchUrl,
  routes,
} from "../../app/navigation/routes";
import { currentAuthSession } from "../../auth/session";
import {
  PositionPageRuntimeRegistry,
  type PositionController,
} from "../../features/positions/runtime";
import {
  ScoreEvidenceController,
  type ScoreEvidenceControllerState,
} from "../../features/positions/score-evidence-controller";
import { PositionStateStore } from "../../features/positions/state-store";
import {
  PositionWorkspaceController,
  type PositionWorkspaceState,
} from "../../features/positions/workspace-controller";

interface PositionWorkspacePageData {
  workspace: PositionWorkspaceState | null;
  score: ScoreEvidenceControllerState | null;
}

interface PositionWorkspacePageCustom {
  retryPositions(): void;
  retryQualification(): void;
  openSearch(): void;
  openQualificationProfile(): void;
  openGapAction(event: WechatMiniprogram.BaseEvent): void;
  openPosition(event: WechatMiniprogram.BaseEvent): void;
  toggleComparison(event: WechatMiniprogram.BaseEvent): void;
  clearComparison(): void;
  openComparison(): void;
  openOnboarding(): void;
  openScoreSheet(): void;
  closeScoreSheet(): void;
  retryScoreHistory(): void;
  beginScoreCorrection(event: WechatMiniprogram.BaseEvent): void;
  startNewScoreEvidence(): void;
  inputXingceScore(event: WechatMiniprogram.Input): void;
  inputShenlunScore(event: WechatMiniprogram.Input): void;
  chooseScoreContext(event: WechatMiniprogram.BaseEvent): void;
  chooseTimedStatus(event: WechatMiniprogram.BaseEvent): void;
  chooseScoreDate(event: WechatMiniprogram.CustomEvent<{ value: string }>): void;
  submitScoreEvidence(): void;
  noop(): void;
}

type PositionWorkspacePage = WechatMiniprogram.Page.Instance<
  PositionWorkspacePageData,
  PositionWorkspacePageCustom
>;

interface WorkspacePageController extends PositionController {
  workspace: PositionWorkspaceController;
  score: ScoreEvidenceController;
  hasShown: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionWorkspacePage, WorkspacePageController>();

Page<PositionWorkspacePageData, PositionWorkspacePageCustom>({
  data: {
    workspace: null,
    score: null,
  },

  onLoad() {
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: loginUrlForReturn(routes.positions) });
      return;
    }
    const runtime = runtimes.attach(this);
    const store = new PositionStateStore(session.userId);
    const client = new ApiClient(undefined, session.accessToken);
    const workspace = new PositionWorkspaceController({
      qualificationApi: new QualificationApi(client),
      store,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ workspace: state });
      },
    });
    const score = new ScoreEvidenceController({
      api: new AbilityApi(client),
      store,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ score: state });
      },
      onSaved: () => workspace.refreshPositions(),
    });
    runtime.controller = {
      workspace,
      score,
      hasShown: false,
      dispose() {
        workspace.dispose();
        score.dispose();
      },
    };
    this.setData({ score: score.snapshot() });
    void workspace.load();
  },

  onShow() {
    const controller = runtimes.get(this)?.controller;
    if (!controller) return;
    if (!controller.hasShown) {
      controller.hasShown = true;
      return;
    }
    void controller.workspace.load();
  },

  onUnload() {
    runtimes.detach(this);
  },

  retryPositions() {
    void runtimes.get(this)?.controller?.workspace.refreshPositions();
  },

  retryQualification() {
    void runtimes.get(this)?.controller?.workspace.loadQualificationGaps();
  },

  openSearch() {
    void wx.navigateTo({ url: positionSearchUrl() });
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
  },

  openGapAction(event) {
    const action = String(event.currentTarget.dataset.action ?? "");
    if (action === "complete_profile") {
      this.openQualificationProfile();
      return;
    }
    const code = String(event.currentTarget.dataset.code ?? "");
    void wx.navigateTo({ url: positionSearchUrl({ query: code }) });
  },

  openPosition(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    if (!positionId) return;
    void wx.navigateTo({ url: positionDetailUrl(positionId) });
  },

  toggleComparison(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    const result = runtimes.get(this)?.controller?.workspace.toggleComparison(positionId);
    if (result?.reason === "limit") {
      void wx.showToast({ title: "最多同时对比 3 个岗位", icon: "none" });
    }
  },

  clearComparison() {
    runtimes.get(this)?.controller?.workspace.clearComparison();
  },

  openComparison() {
    const ids = this.data.workspace?.selectedIds ?? [];
    if (ids.length < 2) return;
    void wx.navigateTo({ url: positionCompareUrl(ids) });
  },

  openOnboarding() {
    void wx.navigateTo({ url: routes.onboarding });
  },

  openScoreSheet() {
    runtimes.get(this)?.controller?.score.open();
  },

  closeScoreSheet() {
    runtimes.get(this)?.controller?.score.close();
  },

  retryScoreHistory() {
    void runtimes.get(this)?.controller?.score.loadHistory();
  },

  beginScoreCorrection(event) {
    runtimes
      .get(this)
      ?.controller?.score.beginCorrection(String(event.currentTarget.dataset.id ?? ""));
  },

  startNewScoreEvidence() {
    runtimes.get(this)?.controller?.score.startNew();
  },

  inputXingceScore(event) {
    runtimes.get(this)?.controller?.score.update({ xingceScore: event.detail.value });
  },

  inputShenlunScore(event) {
    runtimes.get(this)?.controller?.score.update({ shenlunScore: event.detail.value });
  },

  chooseScoreContext(event) {
    runtimes
      .get(this)
      ?.controller?.score.update({ scoreContext: String(event.currentTarget.dataset.value ?? "") });
  },

  chooseTimedStatus(event) {
    runtimes.get(this)?.controller?.score.update({
      scoreTimedStatus: String(event.currentTarget.dataset.value ?? ""),
    });
  },

  chooseScoreDate(event) {
    runtimes.get(this)?.controller?.score.update({ scoreDate: event.detail.value });
  },

  submitScoreEvidence() {
    void runtimes.get(this)?.controller?.score.submit();
  },

  noop() {},
});
