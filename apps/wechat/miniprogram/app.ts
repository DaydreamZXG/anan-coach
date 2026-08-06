import { ApiClient } from "./api/client";
import { installUnauthorizedReset } from "./api/auth";
import { ExternalLearningApi } from "./api/external-learning";
import { configureForWechatEnvVersion, environment } from "./config/environment";
import { recordCoachNotificationOpen } from "./features/coach-notifications/open-tracker";
import { configureExternalLearningGatewayFactory } from "./features/external-learning/contracts";
import { productFontFaceManager, type ProductFontLoadState } from "./platform/product-font";

export interface CoachAppState {
  accessToken?: string;
  userId?: string;
  displayName?: string;
  requestId?: string;
  productFont: Readonly<ProductFontLoadState>;
  statusBarHeight: number;
  navBarHeight: number;
  capsuleInset: number;
}

let networkStatusListenerAttached = false;

function handleNetworkStatusChange(status: WechatMiniprogram.OnNetworkStatusChangeListenerResult) {
  if (status.isConnected) {
    void productFontFaceManager.retry(environment.fontAssets, "network_reconnected");
  }
}

function attachNetworkStatusListener() {
  if (networkStatusListenerAttached) return;
  wx.onNetworkStatusChange(handleNetworkStatusChange);
  networkStatusListenerAttached = true;
}

function detachNetworkStatusListener() {
  if (!networkStatusListenerAttached) return;
  wx.offNetworkStatusChange(
    handleNetworkStatusChange as unknown as WechatMiniprogram.OffNetworkStatusChangeCallback,
  );
  networkStatusListenerAttached = false;
}

App<{ globalData: CoachAppState }>({
  globalData: {
    productFont: productFontFaceManager.snapshot(),
    statusBarHeight: 20,
    navBarHeight: 44,
    capsuleInset: 96,
  },

  onLaunch(options) {
    installUnauthorizedReset();

    const envVersion = wx.getAccountInfoSync().miniProgram.envVersion;
    configureForWechatEnvVersion(envVersion);
    configureExternalLearningGatewayFactory(
      (context) =>
        new ExternalLearningApi(new ApiClient(environment.apiBaseUrl, context.accessToken)),
    );
    const windowInfo = wx.getWindowInfo();
    this.globalData.statusBarHeight = windowInfo.statusBarHeight;
    const capsule = wx.getMenuButtonBoundingClientRect();
    this.globalData.capsuleInset = windowInfo.windowWidth - capsule.left + 8;

    const realtimeLogger = wx.getRealtimeLogManager();
    realtimeLogger.addFilterMsg("product-font");
    productFontFaceManager.subscribe((state) => {
      this.globalData.productFont = state;
      if (state.status === "failed" || state.status === "invalid_configuration") {
        realtimeLogger.error("product-font-unavailable", {
          attempt: state.attempt,
          faces: state.faces,
          trigger: state.lastTrigger,
        });
      } else if (state.status === "loaded") {
        realtimeLogger.info("product-font-loaded", {
          attempt: state.attempt,
          faces: state.faces,
          trigger: state.lastTrigger,
        });
      }
    });
    attachNetworkStatusListener();
    void productFontFaceManager.load(environment.fontAssets, "app_launch");
    deferCoachNotificationOpen(options);
  },

  onShow(options) {
    attachNetworkStatusListener();
    void productFontFaceManager.retry(environment.fontAssets, "app_show_retry");
    deferCoachNotificationOpen(options);
  },

  onHide() {
    detachNetworkStatusListener();
  },
});

function deferCoachNotificationOpen(options: { query?: Record<string, string | undefined> }) {
  // 推送打开记录非首屏关键路径；用 nextTick 推到渲染后再同步读 storage，避免阻塞启动。
  if (typeof wx.nextTick === "function") {
    wx.nextTick(() => {
      void recordCoachNotificationOpen(options).catch(() => undefined);
    });
    return;
  }
  setTimeout(() => {
    void recordCoachNotificationOpen(options).catch(() => undefined);
  }, 0);
}
