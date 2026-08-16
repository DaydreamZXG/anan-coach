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
import {
  PositionCompareController,
  type PositionCompareState,
} from "../features/compare-controller";
import { PositionPageRuntimeRegistry, type PositionController } from "../features/runtime";
import { PositionStateStore } from "../../../features/positions/state-store";

interface PositionComparePageData {
  state: PositionCompareState | null;
}

interface PositionComparePageCustom {
  goBack(): void;
  retry(): void;
  openQualificationProfile(): void;
  openPreparation(): void;
  openPosition(event: WechatMiniprogram.BaseEvent): void;
  choosePosition(event: WechatMiniprogram.BaseEvent): void;
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
    const positionIds = decodeComparisonIds(options.ids);
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

  goBack() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.redirectTo({ url: routes.positions });
  },

  retry() {
    void runtimes.get(this)?.controller?.comparison.load();
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
  },

  openPreparation() {
    void wx.switchTab({ url: routes.ability });
  },

  openPosition(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    if (positionId) void wx.navigateTo({ url: positionDetailUrl(positionId) });
  },

  choosePosition(event) {
    const positionId = String(event.currentTarget.dataset.id ?? "");
    if (positionId) void wx.navigateTo({ url: positionGoalUrl(positionId) });
  },

  returnToSearch() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.redirectTo({ url: routes.positionSearch });
  },
});

function comparisonLoginUrl(positionIds: string[]): string {
  try {
    return loginUrlForReturn(positionCompareUrl(positionIds));
  } catch {
    return routes.login;
  }
}

function decodeComparisonIds(value: string | undefined): string[] {
  if (!value) return [];
  let decoded: string;
  try {
    decoded = decodeURIComponent(value);
  } catch {
    return [];
  }
  return decoded.split(",").map((item) => item.trim());
}
