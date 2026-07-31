export type ReleaseChannel = "development" | "staging" | "production";

export interface FontAssetConfiguration {
  mapleMonoCnRegularUrl: string | null;
  mapleMonoCnBoldUrl: string | null;
  ownedHttpsOrigins: string[];
}

export interface Environment {
  apiBaseUrl: string;
  fontAssets: FontAssetConfiguration;
  releaseChannel: ReleaseChannel;
  supportGroupQrImageUrl: string | null;
  virtualPaymentEnabled: boolean;
}

export const environment: Environment = {
  apiBaseUrl: "https://kg.xingguoxian.cn/staging",
  fontAssets: {
    mapleMonoCnRegularUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-cn-regular-v7.9.woff",
    mapleMonoCnBoldUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-cn-bold-v7.9.woff",
    ownedHttpsOrigins: ["https://kg.xingguoxian.cn"],
  },
  releaseChannel: "development",
  supportGroupQrImageUrl: null,
  virtualPaymentEnabled: false,
};

export function configureForWechatEnvVersion(
  envVersion: "develop" | "trial" | "release",
): Environment {
  const resolved: Environment = {
    apiBaseUrl: environment.apiBaseUrl,
    fontAssets: {
      mapleMonoCnRegularUrl: environment.fontAssets.mapleMonoCnRegularUrl,
      mapleMonoCnBoldUrl: environment.fontAssets.mapleMonoCnBoldUrl,
      ownedHttpsOrigins: [...environment.fontAssets.ownedHttpsOrigins],
    },
    supportGroupQrImageUrl: environment.supportGroupQrImageUrl,
    virtualPaymentEnabled: environment.virtualPaymentEnabled,
    releaseChannel:
      envVersion === "release" ? "production" : envVersion === "trial" ? "staging" : "development",
  };
  Object.assign(environment, resolved);
  return resolved;
}
