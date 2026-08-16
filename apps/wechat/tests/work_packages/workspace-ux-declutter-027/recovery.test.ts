import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import meTemplate from "../../../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../../../miniprogram/pages/today/index.wxml?raw";

describe("workspace UX declutter recovery", () => {
  it("restores optional context through quiet local actions without adding a gate", () => {
    expect(todayTemplate).toContain('class="header-weather header-weather--action"');
    expect(todayTemplate).toContain('bindtap="requestWeatherLocation"');
    expect(todayTemplate).toContain('bind:action="retry"');
    expect(todayTemplate).not.toContain('bindtap="loadDailyOpening"');
    expect(meTemplate).toContain('bindtap="retryProfileContext"');
    expect(meTemplate).toContain('bindtap="openQualificationProfile"');
    expect(meTemplate).toContain("不影响开始学习");
  });
});
