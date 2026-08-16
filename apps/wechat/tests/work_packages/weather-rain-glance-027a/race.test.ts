import { describe, expect, it } from "vitest";

import { presentDailyOpening } from "../../../miniprogram/features/daily-opening/presentation";
import { weatherOpening } from "./fixture";

describe("weather rain glance response ordering", () => {
  it("keeps each out-of-order projection isolated to its own input", () => {
    const inputs = [0.81, 0.07, 0.56].map((precipitationChance) =>
      weatherOpening({ precipitationChance }),
    );
    const [highRain, lowRain, mediumRain] = inputs;
    if (!highRain || !lowRain || !mediumRain) throw new Error("Expected three weather inputs.");
    const labels = [mediumRain, highRain, lowRain].map(
      (input) => presentDailyOpening(input).weatherCompactLabel,
    );

    expect(labels).toEqual(["多云 · 降雨56% · 31°", "多云 · 降雨81% · 31°", "多云 · 降雨7% · 31°"]);
  });
});
