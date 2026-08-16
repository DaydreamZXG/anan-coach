import { ApiClient } from "../../../api/client";
import { PlanningApi } from "../../../api/planning";
import { routes, type RegisteredNavigationTarget } from "../../../app/navigation/routes";
import { currentAuthSession } from "../../../auth/session";
import {
  quickDiagnosticTarget,
  staticCoachRecognitionTarget,
} from "../features/coach-recognition-routing";
import {
  storeCoachEntryIntent,
  type CoachRecognitionPath,
} from "../../../features/onboarding/coach-entry";

Page({
  data: {
    openingPath: "",
  },

  onLoad() {
    if (!currentAuthSession()) {
      void wx.reLaunch({ url: routes.login });
    }
  },

  back() {
    if (getCurrentPages().length > 1) {
      void wx.navigateBack();
      return;
    }
    void wx.switchTab({ url: routes.today });
  },

  async chooseRecognitionPath(event: WechatMiniprogram.BaseEvent) {
    const session = currentAuthSession();
    if (!session || this.data.openingPath) {
      if (session) return;
      void wx.reLaunch({ url: routes.login });
      return;
    }
    const path = String(event.currentTarget.dataset.path);
    if (!isCoachRecognitionPath(path)) return;
    storeCoachEntryIntent(session.userId, path);
    this.setData({ openingPath: path });
    try {
      const target =
        path === "quick_diagnostic"
          ? await loadQuickDiagnosticTarget(session.accessToken)
          : staticCoachRecognitionTarget(path);
      await navigateToCoachRecognitionTarget(target);
    } catch (error) {
      console.error("[onboarding] failed to open coach recognition path", { path, error });
      void wx.showModal({
        title: "诊断任务还没准备好",
        content: "岸岸没有拿到这次的真实诊断题，请稍后再试。你的选择已经保留。",
        showCancel: false,
      });
    } finally {
      this.setData({ openingPath: "" });
    }
  },
});

async function loadQuickDiagnosticTarget(accessToken: string): Promise<RegisteredNavigationTarget> {
  const { data } = await new PlanningApi(new ApiClient(undefined, accessToken)).getToday();
  const target = quickDiagnosticTarget(data);
  if (!target) {
    throw new Error("current plan did not produce an executable objective baseline task");
  }
  return target;
}

async function navigateToCoachRecognitionTarget(target: RegisteredNavigationTarget): Promise<void> {
  if (target.mode === "switchTab") {
    await wx.switchTab({ url: target.url });
    return;
  }
  await wx.navigateTo({ url: target.url });
}

function isCoachRecognitionPath(value: string): value is CoachRecognitionPath {
  return (
    value === "score_report" ||
    value === "wrong_question" ||
    value === "quick_diagnostic" ||
    value === "position_first"
  );
}
