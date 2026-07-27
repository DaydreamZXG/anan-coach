export interface WeatherLocation {
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

export async function readWeatherLocation(): Promise<WeatherLocation> {
  const setting = await wx.getSetting();
  if (setting.authSetting["scope.userLocation"] === false) {
    throw new WeatherLocationPermissionDeniedError();
  }
  if (typeof wx.getLocation !== "function") {
    throw new WeatherLocationUnavailableError("当前微信版本不支持定位，请升级微信后再试。");
  }
  return new Promise<WeatherLocation>((resolve, reject) => {
    wx.getLocation({
      type: "wgs84",
      success: ({ latitude, longitude }) => {
        resolve({
          latitude: coarsenCoordinate(latitude),
          longitude: coarsenCoordinate(longitude),
        });
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

export async function openWeatherLocationSetting(): Promise<boolean> {
  const setting = await wx.openSetting();
  return setting.authSetting["scope.userLocation"] === true;
}

function coarsenCoordinate(value: number): number {
  return Math.round(value * 100) / 100;
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
