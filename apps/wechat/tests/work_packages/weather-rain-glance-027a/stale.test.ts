import { describe, expect, it } from "vitest";

import { presentDailyOpening } from "../../../miniprogram/features/daily-opening/presentation";
import { weatherOpening } from "./fixture";

describe("weather rain glance stale input", () => {
  it("never reuses precipitation from an earlier projection", () => {
    const stale = presentDailyOpening(weatherOpening({ precipitationChance: 0.9 }));
    const current = presentDailyOpening(weatherOpening({ precipitationChance: 0.1 }));

    expect(stale.weatherCompactLabel).toContain("降雨90%");
    expect(current.weatherCompactLabel).toContain("降雨10%");
    expect(current.weatherCompactLabel).not.toContain("90%");
  });
});
