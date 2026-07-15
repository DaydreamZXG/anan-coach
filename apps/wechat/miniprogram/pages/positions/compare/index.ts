import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import { positionCompareUrl, positionDetailUrl, routes } from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";
import {
  PositionCompareController,
  type PositionCompareState,
} from "../../../features/positions/compare-controller";
import {
  PositionPageRuntimeRegistry,
  type PositionController,
} from "../../../features/positions/runtime";
import { PositionStateStore } from "../../../features/positions/state-store";

interface PositionComparePageData {
  state: PositionCompareState | null;
}

interface PositionComparePageCustom {
  retry(): void;
  openOnboarding(): void;
  openPosition(event: WechatMiniprogram.BaseEvent): void;
  returnToSearch(): void;
}

type PositionComparePage = WechatMiniprogram.Page.Instance<
  PositionComparePageData,
  PositionComparePageCustom
>;

interface ComparePageController extends PositionController {
  comparison: PositionCompareController;
  hasShown: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionComparePage, ComparePageController>();

Page<PositionComparePageData, PositionComparePageCustom>({
  data: { state: null },

  onLoad(options: Record<string, string | undefined>) {
    const positionIds = (options.ids ?? "").split(",").map((item) => item.trim());
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: comparisonLoginUrl(positionIds) });
      return;
    }
    const runtime = runtimes.attach(this);
    const controller = new PositionCompareController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      store: new PositionStateStore(session.userId),
      positionIds,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ state });
      },
    });
    runtime.controller = {
      comparison: controller,
      hasShown: false,
      dispose: () => {
        controller.dispose();
      },
    };
    this.setData({ state: controller.snapshot() });
    void controller.load();
  },

  onShow() {
    const controller = runtimes.get(this)?.controller;
    if (!controller) return;
    if (!controller.hasShown) {
      controller.hasShown = true;
      return;
    }
    void controller.comparison.load();
  },

  onUnload() {
    runtimes.detach(this);
  },

  retry() {
    void runtimes.get(this)?.controller?.comparison.load();
  },

  openOnboarding() {
    void wx.navigateTo({ url: routes.onboarding });
  },

  openPosition(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    if (positionId) void wx.navigateTo({ url: positionDetailUrl(positionId) });
  },

  returnToSearch() {
    void wx.navigateBack();
  },
});

function comparisonLoginUrl(positionIds: string[]): string {
  try {
    return loginUrlForReturn(positionCompareUrl(positionIds));
  } catch {
    return routes.login;
  }
}
