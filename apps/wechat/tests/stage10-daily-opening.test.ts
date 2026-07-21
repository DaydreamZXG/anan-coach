import { describe, expect, it, vi } from "vitest";
import { ApiClient } from "../miniprogram/api/client";
import { DailyOpeningApi } from "../miniprogram/api/daily-opening";
import type { DailyOpeningView } from "../miniprogram/api/generated/types.gen";
import { DailyOpeningController } from "../miniprogram/features/daily-opening/controller";
import { presentDailyOpening } from "../miniprogram/features/daily-opening/presentation";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import profileTemplate from "../miniprogram/pages/profile/index.wxml?raw";
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
        label: "南昌市红谷滩区",
        latitude: 28.682,
        longitude: 115.858,
        sourceType: "wechat_choose_location",
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

  it("renders weather, the authoritative plan, explicit location and reversible settings", () => {
    const presentation = presentDailyOpening(opening());
    expect(presentation.weatherHeadline).toBe("南昌市红谷滩区 · 多云 31°");
    expect(presentation.planTitle).toBe("资料分析 · 增长率复测");
    expect(presentation.coffeeVisible).toBe(true);
    expect(todayTemplate).toContain('bindtap="chooseWeatherLocation"');
    expect(todayTemplate).toContain('bindtap="startDailyOpening"');
    expect(todayTemplate).toContain('bindtap="declineDailyCoffee"');
    expect(todayTemplate).toContain('bindtap="disableDailyCoffee"');
    expect(todayTemplate).toContain('bindtap="openWeatherAttribution"');
    expect(profileTemplate).toContain('bindtap="manageDailyOpening"');
    expect(profileTemplate).toContain("岸岸每日开场");
    expect(appConfig).toContain('"pages/weather-attribution/index"');
  });
});

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
      attributionUrl: "https://weatherkit.apple.com/legal-attribution.html",
      summary: {
        provider: "apple-weatherkit",
        locationLabel: "南昌市红谷滩区",
        conditionCode: "PartlyCloudy",
        currentCelsius: 31.4,
        minimumCelsius: 26.2,
        maximumCelsius: 34.7,
        precipitationChance: 0.45,
        attributionUrl: "https://weatherkit.apple.com/legal-attribution.html",
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
