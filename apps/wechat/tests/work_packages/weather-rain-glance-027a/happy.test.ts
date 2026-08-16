import { describe, expect, it } from "vitest";

import { presentDailyOpening } from "../../../miniprogram/features/daily-opening/presentation";
import { weatherOpening } from "./fixture";

describe("weather rain glance happy path", () => {
  it("shows condition, rounded rain chance and temperature in one compact label", () => {
    const view = presentDailyOpening(
      weatherOpening({ conditionCode: "PartlyCloudy", precipitationChance: 0.45 }),
    );

    expect(view.weatherCompactLabel).toBe("多云 · 降雨45% · 31°");
  });
});
