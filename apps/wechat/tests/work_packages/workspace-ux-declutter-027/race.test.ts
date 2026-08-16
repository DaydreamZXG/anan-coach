import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplateRaw from "../../../miniprogram/pages/today/index.wxml?raw";

const todayTemplate = rawAsset(todayTemplateRaw);

function rawAsset(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source;
}

describe("workspace UX declutter response races", () => {
  it("keeps optional opening state in the header and authoritative plan state in the task region", () => {
    const weather = todayTemplate.indexOf("openingView.weatherReady");
    const planBoundary = todayTemplate.indexOf('<block wx:elif="{{view}}">');
    const task = todayTemplate.indexOf('class="today-col today-col--main"');

    expect(weather).toBeGreaterThan(-1);
    expect(weather).toBeLessThan(planBoundary);
    expect(task).toBeGreaterThan(planBoundary);
    expect(todayTemplate.slice(planBoundary)).not.toContain("openingStatus");
    expect(todayTemplate.slice(planBoundary)).not.toContain("openingError");
  });
});
