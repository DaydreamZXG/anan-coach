import { configureForWechatEnvVersion, environment } from "./config/environment";
import { ApiClient } from "./api/client";
import { ExternalLearningApi } from "./api/external-learning";
import { configureExternalLearningGatewayFactory } from "./features/external-learning/contracts";
import { productFontFaceManager, type ProductFontLoadState } from "./platform/product-font";

export interface CoachAppState {
  accessToken?: string;
  userId?: string;
  requestId?: string;
  productFont: Readonly<ProductFontLoadState>;
}

App<{ globalData: CoachAppState }>({
  globalData: { productFont: productFontFaceManager.snapshot() },
  onLaunch() {
    const envVersion = wx.getAccountInfoSync().miniProgram.envVersion;
    configureForWechatEnvVersion(envVersion);
    configureExternalLearningGatewayFactory(
      (context) =>
        new ExternalLearningApi(new ApiClient(environment.apiBaseUrl, context.accessToken)),
    );
    if (environment.releaseChannel === "production" && environment.dataMode !== "api") {
      throw new Error("Production builds must use API data mode");
    }
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
    wx.onNetworkStatusChange((status) => {
      if (status.isConnected) {
        void productFontFaceManager.retry(environment.fontAssets, "network_reconnected");
      }
    });
    void productFontFaceManager.load(environment.fontAssets, "app_launch");
  },
  onShow() {
    void productFontFaceManager.retry(environment.fontAssets, "app_show_retry");
  },
});
