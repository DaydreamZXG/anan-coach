import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { DailyOpeningApi } from "../miniprogram/api/daily-opening";
import type { DailyOpeningView } from "../miniprogram/api/generated/types.gen";
import { DailyOpeningController } from "../miniprogram/features/daily-opening/controller";
import { presentDailyOpening } from "../miniprogram/features/daily-opening/presentation";
import {
  readWeatherLocation,
  WeatherLocationPermissionDeniedError,
} from "../miniprogram/platform/weather-location";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program configuration through its raw asset loader.
import appConfig from "../miniprogram/app.json?raw";

describe("step 10 daily opening", () => {
  it("uses authenticated idempotent endpoints without client provider secrets", async () => {
    const requests: WechatMiniprogram.RequestOption[] = [];
    vi.stubGlobal("wx", {
      request: (options: WechatMiniprogram.RequestOption) => {
        requests.push(options);
        options.success?.({
          data: { data: opening(), requestId: "s10-1", generatedAt: "2026-07-21T00:30:00Z" },
          statusCode: 200,
          header: {},
          cookies: [],
          exception: { reasons: [], retryCount: 0 },
          profile: {} as WechatMiniprogram.RequestProfile,
          useHttpDNS: false,
          errMsg: "request:ok",
        });
      },
    });
    const api = new DailyOpeningApi(new ApiClient("https://api.example.test", "access-token-1"));

    await api.get();
    await api.updateWeatherLocation(
      {
        label: "当前位置附近",
        latitude: 28.682,
        longitude: 115.858,
        sourceType: "wechat_fuzzy_location",
      },
      "daily-location-1",
    );

    expect(requests[0]?.url).toBe("https://api.example.test/api/v2/plans/daily-opening");
    expect(requests[1]?.url).toBe(
      "https://api.example.test/api/v2/plans/daily-opening/weather-location",
    );
    expect(requests[1]?.header).toMatchObject({
      Authorization: "Bearer access-token-1",
      "Idempotency-Key": "daily-location-1",
    });
    expect(JSON.stringify(requests)).not.toContain("WEATHERKIT");
    expect(JSON.stringify(requests)).not.toContain("Bearer user-secret");
    vi.unstubAllGlobals();
  });

  it("publishes once, records a server impression once, and persists coffee controls", async () => {
    const states: string[] = [];
    const recordImpression = vi.fn(() => Promise.resolve());
    const declineCoffee = vi.fn(() => Promise.resolve());
    const updatePreference = vi.fn(() => Promise.resolve());
    let key = 0;
    const controller = new DailyOpeningController({
      accessToken: () => "access-token-1",
      get: () => Promise.resolve(opening()),
      recordImpression,
      updateWeatherLocation: () => Promise.resolve(),
      updatePreference,
      declineCoffee,
      createIdempotencyKey: () => Promise.resolve(`daily-key-${String(++key)}`),
      publish: (state) => states.push(state.status),
    });

    await controller.load();
    await controller.recordDisplayed();
    await controller.recordDisplayed();
    await controller.declineCoffee();
    await controller.disableCoffee();

    expect(states).toContain("ready");
    expect(recordImpression).toHaveBeenCalledTimes(1);
    expect(recordImpression).toHaveBeenCalledWith(
      "access-token-1",
      expect.objectContaining({
        planVersionId: "00000000-0000-4000-8000-000000000101",
        preferenceVersionId: "00000000-0000-4000-8000-000000000102",
        coffeeState: "available",
      }),
      "daily-key-1",
    );
    expect(declineCoffee).toHaveBeenCalledOnce();
    expect(updatePreference).toHaveBeenCalledWith(
      "access-token-1",
      {
        enabled: true,
        weatherEnabled: true,
        coffeePromptEnabled: false,
      },
      "daily-key-3",
    );
  });

  it("renders weather as information and keeps the authoritative plan action", () => {
    const presentation = presentDailyOpening(opening());
    expect(presentation.weatherHeadline).toBe("南昌市红谷滩区 · 多云\u00a031°");
    expect(presentation.planTitle).toBe("资料分析 · 增长率复测");
    expect(presentation.coffeeVisible).toBe(true);
    expect(todayTemplate).toContain("{{openingView.weatherHeadline}}");
    expect(todayTemplate).toContain('bindtap="requestWeatherLocation"');
    expect(todayTemplate).toContain('bindtap="startDailyOpening"');
    expect(todayTemplate).toContain('bindtap="declineDailyCoffee"');
    expect(todayTemplate).not.toContain('bindtap="openCoffeeAssistant"');
    expect(todayTemplate).not.toContain("帮我看看");
    expect(todayTemplate).not.toContain("openWeatherAttribution");
    expect(todayTemplate).not.toContain("天气数据来源");
    expect(appConfig).not.toContain('"pages/weather-attribution/index"');
    expect(appConfig).toContain('"scope.userLocation"');
    expect(appConfig).toContain('"getLocation"');
  });

  it("reads approved mini-program location, coarsens it, and distinguishes permission denial", async () => {
    vi.stubGlobal("wx", {
      getSetting: () => Promise.resolve({ authSetting: {} }),
      getLocation: (options: WechatMiniprogram.GetLocationOption) =>
        options.success?.({
          latitude: 28.682,
          longitude: 115.858,
          speed: 0,
          accuracy: 10,
          altitude: 0,
          verticalAccuracy: 10,
          horizontalAccuracy: 10,
          errMsg: "getLocation:ok",
        }),
    });
    await expect(readWeatherLocation()).resolves.toEqual({
      latitude: 28.68,
      longitude: 115.86,
    });

    vi.stubGlobal("wx", {
      getSetting: () => Promise.resolve({ authSetting: { "scope.userLocation": false } }),
    });
    await expect(readWeatherLocation()).rejects.toBeInstanceOf(
      WeatherLocationPermissionDeniedError,
    );
    vi.unstubAllGlobals();
  });
});

describe("today page start action", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("keeps the card expanded and shows an inline error for an unregistered start URL", async () => {
    const navigateTo = vi.fn();
    const switchTab = vi.fn();
    const pageScrollTo = vi.fn();
    const page = await loadTodayPage({ navigateTo, switchTab, pageScrollTo });
    page.data.openingRaw = openingWithStartUrl("/pages/not-registered/index");

    page.startDailyOpening.call(page);

    expect(page.data.openingCollapsed).toBe(false);
    expect(page.data.openingError).toBe("学习入口暂时不可用，请稍后再试。");
    expect(navigateTo).not.toHaveBeenCalled();
    expect(switchTab).not.toHaveBeenCalled();
    expect(pageScrollTo).not.toHaveBeenCalled();
  });

  it("collapses only after navigation succeeds and stays expanded on failure", async () => {
    const navigateTo = vi.fn((options: WechatMiniprogram.NavigateToOption) => {
      options.fail?.({ errMsg: "navigateTo:fail timeout" });
    });
    const page = await loadTodayPage({ navigateTo });
    page.data.openingRaw = openingWithStartUrl("/pages/practice/index?task=task-1");

    page.startDailyOpening.call(page);
    expect(navigateTo).toHaveBeenCalledWith(
      expect.objectContaining({ url: "/pages/practice/index?task=task-1" }),
    );
    expect(page.data.openingCollapsed).toBe(false);
    expect(page.data.openingError).toBe("navigateTo:fail timeout");

    navigateTo.mockImplementation((options: WechatMiniprogram.NavigateToOption) => {
      options.success?.({ errMsg: "navigateTo:ok", eventChannel: {} as never });
    });
    page.startDailyOpening.call(page);
    expect(page.data.openingCollapsed).toBe(true);
    expect(page.data.openingError).toBe("");
  });

  it("scrolls to the existing task heading when the plan starts on the today page", async () => {
    const pageScrollTo = vi.fn();
    const navigateTo = vi.fn();
    const switchTab = vi.fn();
    const page = await loadTodayPage({ pageScrollTo, navigateTo, switchTab });
    page.data.openingRaw = openingWithStartUrl("/pages/today/index");

    page.startDailyOpening.call(page);

    expect(page.data.openingCollapsed).toBe(true);
    expect(pageScrollTo).toHaveBeenCalledWith(
      expect.objectContaining({ selector: ".task-heading" }),
    );
    expect(navigateTo).not.toHaveBeenCalled();
    expect(switchTab).not.toHaveBeenCalled();
  });
});

interface TodayPageDefinition {
  data: Record<string, unknown>;
  startDailyOpening(this: TestTodayPage): void;
}

interface TestTodayPage extends TodayPageDefinition {
  setData(patch: Record<string, unknown>): void;
}

async function loadTodayPage(wxStub: Record<string, unknown>): Promise<TestTodayPage> {
  vi.resetModules();
  let captured: TodayPageDefinition | undefined;
  vi.stubGlobal("Page", (definition: TodayPageDefinition) => {
    captured = definition;
  });
  vi.stubGlobal("getApp", () => ({ globalData: {} }));
  vi.stubGlobal("wx", { getStorageSync: () => "", ...wxStub });
  await import("../miniprogram/pages/today/index");
  if (!captured) throw new Error("Today page definition was not registered");
  const page: TestTodayPage = {
    ...captured,
    data: JSON.parse(JSON.stringify(captured.data)) as Record<string, unknown>,
    setData(patch: Record<string, unknown>) {
      Object.assign(this.data, patch);
    },
  };
  return page;
}

function openingWithStartUrl(startUrl: string): DailyOpeningView {
  const view = opening();
  return { ...view, plan: { ...view.plan, startUrl } };
}

function opening(): DailyOpeningView {
  return {
    localDate: "2026-07-21",
    displayState: "ready",
    planVersionId: "00000000-0000-4000-8000-000000000101",
    locationVersionId: "00000000-0000-4000-8000-000000000103",
    weatherFingerprint: "a".repeat(64),
    greeting: "早上好，今天先把最值钱的 30 分钟拿下来。",
    plan: {
      title: "资料分析 · 增长率复测",
      estimatedMinutes: 30,
      startUrl: "/pages/practice/index?task=task-1",
    },
    preference: {
      preferenceVersionId: "00000000-0000-4000-8000-000000000102",
      versionNo: 2,
      enabled: true,
      weatherEnabled: true,
      coffeePromptEnabled: true,
      coffeeStart: "07:00:00",
      coffeeEnd: "17:00:00",
      timezone: "Asia/Shanghai",
    },
    weatherState: "ready",
    weather: {
      state: "ready",
      attributionUrl: "https://developer.apple.com/weatherkit/data-source-attribution/",
      summary: {
        provider: "apple-weatherkit",
        locationLabel: "南昌市红谷滩区",
        conditionCode: "PartlyCloudy",
        currentCelsius: 31.4,
        minimumCelsius: 26.2,
        maximumCelsius: 34.7,
        precipitationChance: 0.45,
        attributionUrl: "https://developer.apple.com/weatherkit/data-source-attribution/",
        providerName: "Apple Weather",
        readAt: "2026-07-21T00:30:00Z",
        expiresAt: "2026-07-21T00:50:00Z",
      },
    },
    coffeeState: "available",
    coffee: {
      state: "available",
      showPrompt: true,
      prompt: "要不要让岸岸帮你找一杯附近的咖啡？下单前会再给你确认。",
    },
  };
}
