import { describe, expect, it } from "vitest";

import { presentDailyOpening } from "../../../miniprogram/features/daily-opening/presentation";
import { weatherOpening } from "./fixture";

describe("weather rain glance replay", () => {
  it("returns the same compact label for an exact replay", () => {
    const opening = weatherOpening({ precipitationChance: 0.62 });

    expect(presentDailyOpening(opening).weatherCompactLabel).toBe(
      presentDailyOpening(opening).weatherCompactLabel,
    );
    expect(presentDailyOpening(opening).weatherCompactLabel).toContain("降雨62%");
  });
});
