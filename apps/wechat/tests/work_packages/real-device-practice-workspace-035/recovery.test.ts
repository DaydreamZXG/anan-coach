import { describe, expect, it } from "vitest";

import { formatPercentRatio } from "../../../miniprogram/pages/mock-exam/format";

describe("RU-REAL-DEVICE-PRACTICE-WORKSPACE-035 result recovery", () => {
  it("formats module accuracy without exposing floating-point tails", () => {
    expect(formatPercentRatio(1 / 30)).toBe("3.33%");
    expect(formatPercentRatio(1 / 15)).toBe("6.67%");
    expect(formatPercentRatio(1 / 4)).toBe("25%");
    expect(formatPercentRatio(0)).toBe("0%");
    expect(formatPercentRatio(Number.NaN)).toBe("--");
  });
});
