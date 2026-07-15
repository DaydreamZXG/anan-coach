import { configureForWechatEnvVersion, environment } from "./config/environment";
import { dataFontFaceManager, type DataFontLoadState } from "./platform/data-font";

export interface CoachAppState {
  accessToken?: string;
  userId?: string;
  requestId?: string;
  dataFont: Readonly<DataFontLoadState>;
}

App<{ globalData: CoachAppState }>({
  globalData: { dataFont: dataFontFaceManager.snapshot() },
  onLaunch() {
    const envVersion = wx.getAccountInfoSync().miniProgram.envVersion;
    configureForWechatEnvVersion(envVersion);
    if (environment.releaseChannel === "production" && environment.dataMode !== "api") {
      throw new Error("Production builds must use API data mode");
    }
    const realtimeLogger = wx.getRealtimeLogManager();
    realtimeLogger.addFilterMsg("data-font");
    dataFontFaceManager.subscribe((state) => {
      this.globalData.dataFont = state;
      if (state.status === "failed" || state.status === "invalid_configuration") {
        realtimeLogger.error("data-font-unavailable", {
          attempt: state.attempt,
          resultStatus: state.resultStatus,
          sourceUrl: state.sourceUrl,
          trigger: state.lastTrigger,
        });
      } else if (state.status === "loaded") {
        realtimeLogger.info("data-font-loaded", {
          attempt: state.attempt,
          sourceUrl: state.sourceUrl,
          trigger: state.lastTrigger,
        });
      }
    });
    wx.onNetworkStatusChange((status) => {
      if (status.isConnected) {
        void dataFontFaceManager.retry(environment.fontAssets, "network_reconnected");
      }
    });
    void dataFontFaceManager.load(environment.fontAssets, "app_launch");
  },
  onShow() {
    void dataFontFaceManager.retry(environment.fontAssets, "app_show_retry");
  },
});
