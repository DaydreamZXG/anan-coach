import type { DailyOpeningView } from "../../../miniprogram/api/generated/types.gen";

interface WeatherOverrides {
  conditionCode?: string;
  currentCelsius?: number;
  precipitationChance?: number;
  summary?: boolean;
}

export function weatherOpening(overrides: WeatherOverrides = {}): DailyOpeningView {
  const summary: DailyOpeningView["weather"]["summary"] =
    overrides.summary === false
      ? null
      : {
          provider: "apple-weatherkit" as const,
          locationLabel: "南昌市红谷滩区",
          conditionCode: overrides.conditionCode ?? "PartlyCloudy",
          currentCelsius: overrides.currentCelsius ?? 31.4,
          minimumCelsius: 26.2,
          maximumCelsius: 34.7,
          precipitationChance: overrides.precipitationChance ?? 0.45,
          attributionUrl: "https://developer.apple.com/weatherkit/data-source-attribution/",
          providerName: "Apple Weather",
          readAt: "2026-08-15T09:30:00Z",
          expiresAt: "2026-08-15T09:50:00Z",
        };
  return {
    localDate: "2026-08-15",
    displayState: "ready",
    planVersionId: "00000000-0000-4000-8000-000000000201",
    locationVersionId: "00000000-0000-4000-8000-000000000202",
    weatherFingerprint: "b".repeat(64),
    greeting: "下午好",
    plan: {
      title: "申论材料阅读",
      estimatedMinutes: 15,
      startUrl: "/pages/practice/index?task=task-027a",
    },
    preference: {
      preferenceVersionId: "00000000-0000-4000-8000-000000000203",
      versionNo: 1,
      enabled: true,
      weatherEnabled: true,
      coffeePromptEnabled: false,
      coffeeStart: "07:00:00",
      coffeeEnd: "17:00:00",
      timezone: "Asia/Shanghai",
    },
    weatherState: "ready",
    weather: {
      state: "ready",
      attributionUrl: "https://developer.apple.com/weatherkit/data-source-attribution/",
      summary,
    },
    coffeeState: "disabled",
    coffee: { state: "disabled", showPrompt: false, prompt: "" },
  };
}

export function locationRequiredOpening(): DailyOpeningView {
  const view = weatherOpening({ summary: false });
  return {
    ...view,
    weatherState: "location_required",
    weather: { state: "location_required", attributionUrl: "", summary: null },
  };
}
