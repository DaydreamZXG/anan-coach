import { ApiClient, ApiClientError } from "../../api/client";
import { XingceLearningApi, type XingceBriefingMode } from "../../api/xingce-learning";
import { routes } from "../../app/navigation/routes";
import { currentAuthSession, type AuthSession } from "../../auth/session";
import { environment } from "../../config/environment";
import {
  assertXingceTaskBriefing,
  briefingExpired,
  isValidBriefingPlanTaskId,
  parseXingceBriefingMode,
  xingcePracticeUrl,
} from "../../features/xingce/briefing/contracts";
import {
  XingceBriefingPageRuntimeRegistry,
  type XingceBriefingPageRuntime,
} from "../../features/xingce/briefing/page-runtime";
import {
  presentXingceTaskBriefing,
  type XingceTaskBriefingView,
} from "../../features/xingce/briefing/presentation";

type BriefingPageStatus = "default" | "loading" | "ready" | "error" | "expired" | "unavailable";

interface XingceTaskIntroPageData {
  status: BriefingPageStatus;
  view: XingceTaskBriefingView | null;
  errorTitle: string;
  errorMessage: string;
  requestId: string;
  showLoginAction: boolean;
  canRetry: boolean;
  recovered: boolean;
  starting: boolean;
}

interface XingceTaskIntroPageCustom {
  retry(): void;
  goLogin(): void;
  startTask(): Promise<void>;
  returnToday(): void;
}

type XingceTaskIntroPage = WechatMiniprogram.Page.Instance<
  XingceTaskIntroPageData,
  XingceTaskIntroPageCustom
>;

const runtimes = new XingceBriefingPageRuntimeRegistry<XingceTaskIntroPage>();

Page<XingceTaskIntroPageData, XingceTaskIntroPageCustom>({
  data: initialData(),

  onLoad(options: Record<string, string | undefined>) {
    const planTaskId = options.planTaskId?.trim();
    const requestedMode = parseXingceBriefingMode(options.mode);
    if (!isValidBriefingPlanTaskId(planTaskId) || !requestedMode) {
      this.setData({
        status: "error",
        errorTitle: "任务说明地址不完整",
        errorMessage: "缺少明确的行测计划任务或说明模式，页面不会猜测要打开哪项任务。",
        canRetry: false,
      });
      return;
    }
    if (environment.dataMode === "prototype") {
      this.setData({
        status: "unavailable",
        errorTitle: "演示数据不能创建真实训练",
        errorMessage: "任务范围、验收合同和有效期必须来自已认证服务端。",
      });
      return;
    }
    const session = currentAuthSession();
    if (!session) {
      showLoggedOut(this, "登录后才能读取当前计划的任务说明。");
      return;
    }
    const runtime = attachRuntime(this, session, planTaskId, requestedMode);
    void loadBriefing(this, runtime);
  },

  onShow() {
    if (environment.dataMode === "prototype") return;
    const runtime = runtimes.get(this);
    if (!runtime) return;
    const session = currentAuthSession();
    if (!session) {
      runtimes.detach(this);
      showLoggedOut(this, "登录状态已失效，旧账号的任务说明已从页面清除。");
      return;
    }
    if (session.userId !== runtime.ownerUserId) {
      const replacement = attachRuntime(this, session, runtime.planTaskId, runtime.requestedMode);
      this.setData(initialData());
      void loadBriefing(this, replacement);
      return;
    }
    if (runtime.briefing && briefingExpired(runtime.briefing)) showClientExpired(this);
  },

  onUnload() {
    runtimes.detach(this);
  },

  retry() {
    const runtime = runtimes.get(this);
    if (runtime) void loadBriefing(this, runtime);
  },

  goLogin() {
    void wx.redirectTo({ url: routes.login });
  },

  async startTask() {
    const runtime = runtimes.get(this);
    const briefing = runtime?.briefing;
    if (!runtime || !briefing || this.data.starting || !currentAccountOwns(runtime)) return;
    if (briefingExpired(briefing)) {
      showClientExpired(this);
      return;
    }
    this.setData({ starting: true });
    try {
      await wx.navigateTo({ url: xingcePracticeUrl(briefing) });
    } finally {
      if (runtimes.owns(this, runtime)) this.setData({ starting: false });
    }
  },

  returnToday() {
    void wx.switchTab({ url: routes.today });
  },
});

function attachRuntime(
  page: XingceTaskIntroPage,
  session: AuthSession,
  planTaskId: string,
  requestedMode: XingceBriefingMode,
): XingceBriefingPageRuntime {
  return runtimes.attach(page, {
    api: new XingceLearningApi(new ApiClient(environment.apiBaseUrl, session.accessToken)),
    ownerUserId: session.userId,
    planTaskId,
    requestedMode,
    briefing: null,
    requestEpoch: 0,
    requestInFlight: false,
    disposed: false,
    hadLoadFailure: false,
  });
}

async function loadBriefing(
  page: XingceTaskIntroPage,
  runtime: XingceBriefingPageRuntime,
): Promise<void> {
  if (runtime.requestInFlight) return;
  const epoch = ++runtime.requestEpoch;
  runtime.requestInFlight = true;
  runtime.briefing = null;
  page.setData({
    status: "loading",
    view: null,
    errorTitle: "",
    errorMessage: "",
    requestId: "",
    showLoginAction: false,
    recovered: false,
    starting: false,
    canRetry: true,
  });
  try {
    const response = await runtime.api.getTaskBriefing(runtime.planTaskId);
    if (!runtimes.owns(page, runtime, epoch) || !currentAccountOwns(runtime)) return;
    const briefing = assertXingceTaskBriefing(response.data, runtime.planTaskId);
    runtime.briefing = briefing;
    if (briefingExpired(briefing)) {
      showClientExpired(page);
      return;
    }
    page.setData({
      status: "ready",
      view: presentXingceTaskBriefing(briefing),
      errorTitle: "",
      errorMessage: "",
      requestId: "",
      showLoginAction: false,
      recovered: runtime.hadLoadFailure,
    });
    runtime.hadLoadFailure = false;
  } catch (error) {
    if (!runtimes.owns(page, runtime, epoch)) return;
    if (!currentAccountOwns(runtime)) {
      runtimes.detach(page);
      showLoggedOut(page, "账号已经变更，旧账号的任务说明已从页面清除。");
      return;
    }
    runtime.hadLoadFailure = true;
    if (error instanceof ApiClientError && error.statusCode === 401) {
      runtimes.detach(page);
      showLoggedOut(page, "登录状态已过期，请重新登录后读取任务说明。");
      return;
    }
    if (error instanceof ApiClientError && error.statusCode === 409) {
      runtime.briefing = null;
      page.setData({
        status: "expired",
        view: null,
        errorTitle: "任务说明已经失效",
        errorMessage:
          "服务端确认该任务已过期、证据版本已变化或已不属于当前计划。请返回今日获取新任务。",
        requestId: error.requestId,
        canRetry: false,
      });
      return;
    }
    page.setData({
      status: "error",
      view: null,
      errorTitle: "任务说明尚未载入",
      errorMessage: error instanceof Error ? error.message : "服务端没有返回可确认的任务说明。",
      requestId: error instanceof ApiClientError ? error.requestId : "",
      canRetry: true,
    });
  } finally {
    if (runtimes.owns(page, runtime, epoch)) runtime.requestInFlight = false;
  }
}

function currentAccountOwns(runtime: XingceBriefingPageRuntime): boolean {
  return currentAuthSession()?.userId === runtime.ownerUserId;
}

function showClientExpired(page: XingceTaskIntroPage): void {
  page.setData({
    status: "expired",
    errorTitle: "设备时间显示任务说明已到期",
    errorMessage: "请重新读取服务端权威说明；是否仍可执行最终以服务端返回为准。",
    requestId: "",
    starting: false,
    canRetry: true,
  });
}

function showLoggedOut(page: XingceTaskIntroPage, message: string): void {
  page.setData({
    ...initialData(),
    status: "unavailable",
    errorTitle: "需要重新登录",
    errorMessage: message,
    showLoginAction: true,
    canRetry: false,
  });
}

function initialData(): XingceTaskIntroPageData {
  return {
    status: "default",
    view: null,
    errorTitle: "",
    errorMessage: "",
    requestId: "",
    showLoginAction: false,
    canRetry: false,
    recovered: false,
    starting: false,
  };
}
