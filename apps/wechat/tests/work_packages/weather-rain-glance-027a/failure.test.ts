import { describe, expect, it } from "vitest";

import { presentDailyOpening } from "../../../miniprogram/features/daily-opening/presentation";
import { weatherOpening } from "./fixture";

describe("weather rain glance failure", () => {
  it("does not invent rain or temperature when the weather summary is absent", () => {
    const view = presentDailyOpening(weatherOpening({ summary: false }));

    expect(view.weatherReady).toBe(false);
    expect(view.weatherCompactLabel).toBe("");
  });
});
