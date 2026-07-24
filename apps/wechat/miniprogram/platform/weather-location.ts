export interface FuzzyWeatherLocation {
  latitude: number;
  longitude: number;
}

export class WeatherLocationPermissionDeniedError extends Error {
  constructor() {
    super("你没有允许岸岸获取附近位置。");
    this.name = "WeatherLocationPermissionDeniedError";
  }
}

export class WeatherLocationUnavailableError extends Error {
  constructor(message = "暂时无法获取附近位置。") {
    super(message);
    this.name = "WeatherLocationUnavailableError";
  }
}

export async function readFuzzyWeatherLocation(): Promise<FuzzyWeatherLocation> {
  const setting = await wx.getSetting();
  if (setting.authSetting["scope.userFuzzyLocation"] === false) {
    throw new WeatherLocationPermissionDeniedError();
  }
  if (typeof wx.getFuzzyLocation !== "function") {
    throw new WeatherLocationUnavailableError("当前微信版本不支持模糊定位，请升级微信后再试。");
  }
  return new Promise<FuzzyWeatherLocation>((resolve, reject) => {
    wx.getFuzzyLocation({
      type: "wgs84",
      success: ({ latitude, longitude }) => {
        resolve({ latitude, longitude });
      },
      fail: (error) => {
        if (isPermissionDenial(error)) {
          reject(new WeatherLocationPermissionDeniedError());
          return;
        }
        reject(new WeatherLocationUnavailableError());
      },
    });
  });
}

export async function openFuzzyWeatherLocationSetting(): Promise<boolean> {
  const setting = await wx.openSetting();
  return setting.authSetting["scope.userFuzzyLocation"] === true;
}

function isPermissionDenial(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const rawMessage = (error as { errMsg?: unknown }).errMsg;
  const message = typeof rawMessage === "string" ? rawMessage.toLowerCase() : "";
  return (
    message.includes("auth deny") ||
    message.includes("auth denied") ||
    message.includes("permission denied") ||
    message.includes("authorize no response")
  );
}
