import { ApiClient } from "../../../api/client";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import { positionBriefingUrl, positionDetailUrl, routes } from "../../../app/navigation/routes";
import { PageControllerRegistry } from "../../../app/runtime/controller-registry";
import { currentAuthSession } from "../../../auth/session";
import {
  PositionBriefingController,
  type PositionBriefingState,
} from "../../../features/positions/briefing-controller";

interface PositionBriefingPageData {
  state: PositionBriefingState | null;
}

interface PositionBriefingPageCustom {
  retry(): void;
  openPositionEvidence(): void;
  enterToday(): void;
}

type PositionBriefingPage = WechatMiniprogram.Page.Instance<
  PositionBriefingPageData,
  PositionBriefingPageCustom
>;

const runtimes = new PageControllerRegistry<PositionBriefingPage, PositionBriefingController>();

Page<PositionBriefingPageData, PositionBriefingPageCustom>({
  data: { state: null },

  onLoad(options: Record<string, string | undefined>) {
    const positionId = options.positionId?.trim() ?? "";
    const session = currentAuthSession();
    if (!session) {
      void wx.reLaunch({ url: briefingLoginUrl(positionId) });
      return;
    }
    const runtime = runtimes.attach(this);
    const controller = new PositionBriefingController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      positionId,
      onChange: (state) => {
        if (runtimes.owns(this, runtime)) this.setData({ state });
      },
    });
    runtime.controller = controller;
    this.setData({ state: controller.snapshot() });
    void controller.load();
  },

  onUnload() {
    runtimes.detach(this);
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },

  openPositionEvidence() {
    const positionId = this.data.state?.positionId;
    if (positionId) void wx.redirectTo({ url: positionDetailUrl(positionId) });
  },

  enterToday() {
    void wx.switchTab({ url: routes.today });
  },
});

function briefingLoginUrl(positionId: string): string {
  if (!positionId) return routes.login;
  try {
    return loginUrlForReturn(positionBriefingUrl(positionId));
  } catch {
    return routes.login;
  }
}
