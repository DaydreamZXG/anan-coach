import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import {
  positionBriefingUrl,
  positionDetailUrl,
  positionGoalUrl,
  routes,
} from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";
import {
  PositionGoalController,
  type PositionGoalState,
} from "../../../features/positions/goal-controller";
import {
  PositionPageRuntimeRegistry,
  type PositionController,
} from "../../../features/positions/runtime";
import { PositionStateStore } from "../../../features/positions/state-store";

interface PositionGoalPageData {
  state: PositionGoalState | null;
}

interface PositionGoalPageCustom {
  retry(): void;
  openOnboarding(): void;
  openDetail(): void;
  confirmGoal(): void;
  openBeginnerBriefing(): void;
  finish(): void;
}

type PositionGoalPage = WechatMiniprogram.Page.Instance<
  PositionGoalPageData,
  PositionGoalPageCustom
>;

interface GoalPageController extends PositionController {
  goal: PositionGoalController;
  hasShown: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionGoalPage, GoalPageController>();

Page<PositionGoalPageData, PositionGoalPageCustom>({
  data: { state: null },

  onLoad(options: Record<string, string | undefined>) {
    const positionId = options.positionId?.trim() ?? "";
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({
        url: positionId ? loginUrlForReturn(positionGoalUrl(positionId)) : routes.login,
      });
      return;
    }
    const runtime = runtimes.attach(this);
    const controller = new PositionGoalController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      store: new PositionStateStore(session.userId),
      positionId,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ state });
      },
    });
    runtime.controller = {
      goal: controller,
      hasShown: false,
      dispose: () => {
        controller.dispose();
      },
    };
    this.setData({ state: controller.snapshot() });
    if (!positionId) {
      void wx.showToast({ title: "目标地址缺少 positionId", icon: "none" });
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
    if (controller.goal.snapshot().status !== "confirmed") {
      void controller.goal.load();
    }
  },

  onUnload() {
    runtimes.detach(this);
  },

  retry() {
    const controller = runtimes.get(this)?.controller?.goal;
    if (!controller) return;
    if (this.data.state?.preview) void controller.confirm();
    else void controller.regenerate();
  },

  openOnboarding() {
    void wx.navigateTo({ url: routes.onboarding });
  },

  openDetail() {
    const positionId = this.data.state?.positionId;
    if (positionId) void wx.redirectTo({ url: positionDetailUrl(positionId) });
  },

  confirmGoal() {
    void runtimes.get(this)?.controller?.goal.confirm();
  },

  openBeginnerBriefing() {
    const positionId = this.data.state?.positionId;
    if (positionId) void wx.navigateTo({ url: positionBriefingUrl(positionId) });
  },

  finish() {
    void wx.switchTab({
      url: this.data.state?.offerExamBriefing ? routes.today : routes.positions,
    });
  },
});
