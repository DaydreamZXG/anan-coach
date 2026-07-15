export type DataMode = "prototype" | "api";
export type ReleaseChannel = "development" | "staging" | "production";

export interface FontAssetConfiguration {
  mapleMonoUrl: string | null;
  ownedHttpsOrigins: string[];
}

export interface Environment {
  apiBaseUrl: string;
  dataMode: DataMode;
  fontAssets: FontAssetConfiguration;
  releaseChannel: ReleaseChannel;
}

export const environment: Environment = {
  apiBaseUrl: "https://kg.xingguoxian.cn",
  dataMode: "api",
  fontAssets: {
    mapleMonoUrl: "https://kg.xingguoxian.cn/assets/fonts/maple-mono-data-v7.9.ttf",
    ownedHttpsOrigins: ["https://kg.xingguoxian.cn"],
  },
  releaseChannel: "development",
};

export function configureForWechatEnvVersion(
  envVersion: "develop" | "trial" | "release",
): Environment {
  const resolved: Environment = {
    apiBaseUrl: environment.apiBaseUrl,
    dataMode: "api",
    fontAssets: {
      mapleMonoUrl: environment.fontAssets.mapleMonoUrl,
      ownedHttpsOrigins: [...environment.fontAssets.ownedHttpsOrigins],
    },
    releaseChannel:
      envVersion === "release" ? "production" : envVersion === "trial" ? "staging" : "development",
  };
  Object.assign(environment, resolved);
  return resolved;
}
