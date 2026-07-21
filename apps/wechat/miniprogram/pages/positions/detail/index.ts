import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import { positionDetailUrl, positionGoalUrl, routes } from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";
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
}

interface PositionDetailPageCustom {
  retry(): void;
  openOnboarding(): void;
  openPositionsWorkspace(): void;
  confirmRequirement(event: WechatMiniprogram.BaseEvent): void;
  openGoalChange(): void;
}

type PositionDetailPage = WechatMiniprogram.Page.Instance<
  PositionDetailPageData,
  PositionDetailPageCustom
>;

interface DetailPageController extends PositionController {
  detail: PositionDetailController;
  hasShown: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionDetailPage, DetailPageController>();

Page<PositionDetailPageData, PositionDetailPageCustom>({
  data: { state: null },

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
    const controller = new PositionDetailController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      store: new PositionStateStore(session.userId),
      positionId,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ state });
      },
    });
    runtime.controller = {
      detail: controller,
      hasShown: false,
      dispose: () => {
        controller.dispose();
      },
    };
    this.setData({ state: controller.snapshot() });
    if (!positionId) {
      void wx.showToast({ title: "岗位地址缺少 positionId", icon: "none" });
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
    void controller.detail.load();
  },

  onUnload() {
    runtimes.detach(this);
  },

  retry() {
    void runtimes.get(this)?.controller?.detail.load();
  },

  openOnboarding() {
    void wx.navigateTo({ url: routes.onboarding });
  },

  openPositionsWorkspace() {
    void wx.switchTab({ url: routes.positions });
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

  openGoalChange() {
    const positionId = this.data.state?.positionId;
    if (positionId) void wx.navigateTo({ url: positionGoalUrl(positionId) });
  },
});
