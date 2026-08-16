import { describe, expect, it } from "vitest";

import { presentDailyOpening } from "../../../miniprogram/features/daily-opening/presentation";
import { locationRequiredOpening, weatherOpening } from "./fixture";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../../../miniprogram/pages/today/index.wxml?raw";

describe("weather rain glance recovery", () => {
  it("offers a clear rain action before location and shows the percentage after recovery", () => {
    const before = presentDailyOpening(locationRequiredOpening());
    const after = presentDailyOpening(weatherOpening({ precipitationChance: 0.72 }));

    expect(before.weatherLocationRequired).toBe(true);
    expect(before.weatherCompactLabel).toBe("");
    expect(todayTemplate).toContain("看降雨");
    expect(after.weatherReady).toBe(true);
    expect(after.weatherCompactLabel).toContain("降雨72%");
  });
});
