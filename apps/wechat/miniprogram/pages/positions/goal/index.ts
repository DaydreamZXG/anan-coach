import { ApiClient } from "../../../api/client";
import type { OnboardingResultProjection } from "../../../api/generated/types.gen";
import { OnboardingApi } from "../../../api/onboarding";
import { QualificationApi } from "../../../api/qualification";
import { loginUrlForReturn } from "../../../app/navigation/auth-return";
import {
  positionBriefingUrl,
  positionDetailUrl,
  positionGoalUrl,
  onboardingResultActionTarget,
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
  postGoalAction: OnboardingResultProjection["nextAction"] | "";
  resolvingPostGoalAction: boolean;
  postGoalError: string;
}

interface PositionGoalPageCustom {
  goBack(): void;
  retry(): void;
  openQualificationProfile(): void;
  openDetail(): void;
  confirmGoal(): void;
  openBeginnerBriefing(): void;
  openRecommendedEntry(): void;
  finish(): void;
}

type PositionGoalPage = WechatMiniprogram.Page.Instance<
  PositionGoalPageData,
  PositionGoalPageCustom
>;

interface GoalPageController extends PositionController {
  goal: PositionGoalController;
  hasShown: boolean;
  postGoalResolutionStarted: boolean;
}

const runtimes = new PositionPageRuntimeRegistry<PositionGoalPage, GoalPageController>();

Page<PositionGoalPageData, PositionGoalPageCustom>({
  data: {
    state: null,
    postGoalAction: "",
    resolvingPostGoalAction: false,
    postGoalError: "",
  },

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
    const onboardingApi = new OnboardingApi(new ApiClient(undefined, session.accessToken));
    const controller = new PositionGoalController({
      api: new QualificationApi(new ApiClient(undefined, session.accessToken)),
      store: new PositionStateStore(session.userId),
      positionId,
      onChange: (state) => {
        if (!runtimes.owns(this, runtime)) return;
        this.setData({ state });
        const pageController = runtime.controller;
        if (
          state.status === "confirmed" &&
          state.offerExamBriefing &&
          pageController &&
          !pageController.postGoalResolutionStarted
        ) {
          pageController.postGoalResolutionStarted = true;
          void resolvePostGoalAction(this, runtime, onboardingApi);
        }
      },
    });
    runtime.controller = {
      goal: controller,
      hasShown: false,
      postGoalResolutionStarted: false,
      dispose: () => {
        controller.dispose();
      },
    };
    this.setData({ state: controller.snapshot() });
    if (!positionId) {
      void wx.showToast({ title: "目标岗位信息不完整，请返回重新选择", icon: "none" });
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

  goBack() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.redirectTo({ url: routes.positions });
  },

  retry() {
    const controller = runtimes.get(this)?.controller?.goal;
    if (!controller) return;
    if (this.data.state?.preview) void controller.confirm();
    else void controller.regenerate();
  },

  openQualificationProfile() {
    void wx.navigateTo({ url: routes.qualificationProfile });
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

  openRecommendedEntry() {
    const action = this.data.postGoalAction;
    if (!action) return;
    const target = onboardingResultActionTarget(action, this.data.state?.positionId);
    if (target.mode === "switchTab") void wx.switchTab({ url: target.url });
    else void wx.redirectTo({ url: target.url });
  },

  finish() {
    if (this.data.state?.offerExamBriefing) {
      void wx.switchTab({ url: routes.today });
    } else {
      void wx.redirectTo({ url: routes.positions });
    }
  },
});

async function resolvePostGoalAction(
  page: PositionGoalPage,
  runtime: ReturnType<typeof runtimes.attach>,
  api: OnboardingApi,
): Promise<void> {
  page.setData({ resolvingPostGoalAction: true, postGoalError: "" });
  try {
    const response = await api.getResult();
    if (!runtimes.owns(page, runtime)) return;
    page.setData({
      postGoalAction: response.data.nextAction,
      resolvingPostGoalAction: false,
      postGoalError: "",
    });
  } catch {
    if (!runtimes.owns(page, runtime)) return;
    page.setData({
      resolvingPostGoalAction: false,
      postGoalError: "主目标已经确认，但首日入口没有读取完成，请重新进入该岗位。",
    });
  }
}
