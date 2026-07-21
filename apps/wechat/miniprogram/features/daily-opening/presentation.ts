import type { DailyOpeningView } from "../../api/generated/types.gen";

export interface DailyOpeningPresentation {
  localDateLabel: string;
  greeting: string;
  planTitle: string;
  planMinutesLabel: string;
  weatherReady: boolean;
  weatherLocationRequired: boolean;
  weatherUnavailable: boolean;
  weatherHeadline: string;
  weatherDetail: string;
  attributionUrl: string;
  attributionVisible: boolean;
  coffeeVisible: boolean;
  coffeePrompt: string;
}

const CONDITION_LABELS: Record<string, string> = {
  Clear: "晴",
  MostlyClear: "晴间多云",
  PartlyCloudy: "多云",
  MostlyCloudy: "阴",
  Cloudy: "阴",
  Drizzle: "小雨",
  Rain: "有雨",
  HeavyRain: "大雨",
  Thunderstorms: "雷阵雨",
  Snow: "有雪",
  Haze: "有霾",
  Foggy: "有雾",
  Windy: "有风",
};

function rounded(value: number): string {
  return String(Math.round(value));
}

export function presentDailyOpening(view: DailyOpeningView): DailyOpeningPresentation {
  const summary = view.weather.summary;
  const condition = summary
    ? (CONDITION_LABELS[summary.conditionCode] ?? summary.conditionCode)
    : "";
  return {
    localDateLabel: view.localDate.slice(5).replace("-", "月") + "日",
    greeting: view.greeting,
    planTitle: view.plan.title,
    planMinutesLabel:
      view.plan.estimatedMinutes > 0 ? `${String(view.plan.estimatedMinutes)} 分钟` : "今日收口",
    weatherReady: view.weather.state === "ready" && Boolean(summary),
    weatherLocationRequired: view.weather.state === "location_required",
    weatherUnavailable: view.weather.state === "provider_unavailable",
    weatherHeadline: summary
      ? `${summary.locationLabel} · ${condition} ${rounded(summary.currentCelsius)}°`
      : "",
    weatherDetail: summary
      ? `${rounded(summary.minimumCelsius)}°–${rounded(summary.maximumCelsius)}° · 降水 ${String(Math.round(summary.precipitationChance * 100))}%`
      : "",
    attributionUrl: view.weather.attributionUrl ?? "",
    attributionVisible: Boolean(view.weather.attributionUrl),
    coffeeVisible: view.coffee.showPrompt && view.coffee.state === "available",
    coffeePrompt: view.coffee.prompt ?? "",
  };
}
