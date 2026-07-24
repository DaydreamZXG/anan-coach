import { ApiClient } from "../../api/client";
import { RetakeApi } from "../../api/retake";
import { routes } from "../../app/navigation/routes";
import { PageControllerRegistry } from "../../app/runtime/controller-registry";
import { currentAuthSession } from "../../auth/session";
import { RetakeController, type RetakeControllerState } from "../../features/retake/controller";

interface RetakePageData {
  state: RetakeControllerState | null;
}

interface RetakePageCustom {
  selectEvidence(event: WechatMiniprogram.BaseEvent): void;
  startProgram(): void;
  importEvidence(): void;
  enterToday(): void;
  retry(): void;
}

type RetakePage = WechatMiniprogram.Page.Instance<RetakePageData, RetakePageCustom>;
const runtimes = new PageControllerRegistry<RetakePage, RetakeController>();

Page<RetakePageData, RetakePageCustom>({
  data: { state: null },

  onLoad() {
    mountController(this);
  },

  onShow() {
    if (!runtimes.get(this)?.controller?.sessionIsCurrent()) mountController(this);
  },

  onUnload() {
    runtimes.detach(this);
  },

  selectEvidence(event) {
    runtimes
      .get(this)
      ?.controller?.selectEvidence(String(event.currentTarget.dataset.evidenceId ?? ""));
  },

  startProgram() {
    void runtimes.get(this)?.controller?.startProgram();
  },

  importEvidence() {
    void wx.navigateTo({ url: routes.externalReport });
  },

  enterToday() {
    void wx.switchTab({ url: routes.today });
  },

  retry() {
    void runtimes.get(this)?.controller?.load();
  },
});

function mountController(page: RetakePage): void {
  const session = currentAuthSession();
  if (!session) {
    runtimes.detach(page);
    void wx.reLaunch({ url: routes.login });
    return;
  }
  const runtime = runtimes.attach(page);
  const controller = new RetakeController({
    userId: session.userId,
    api: new RetakeApi(new ApiClient(undefined, session.accessToken)),
    onChange: (state) => {
      if (runtimes.owns(page, runtime)) page.setData({ state });
    },
    isSessionCurrent: () => {
      const current = currentAuthSession();
      return current?.userId === session.userId && current.accessToken === session.accessToken;
    },
  });
  runtime.controller = controller;
  page.setData({ state: controller.snapshot() });
  void controller.load();
}
