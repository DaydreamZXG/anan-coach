import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import meTemplateRaw from "../../../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplateRaw from "../../../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import todayStylesRaw from "../../../miniprogram/pages/today/index.wxss?raw";

const meTemplate = rawAsset(meTemplateRaw);
const todayTemplate = rawAsset(todayTemplateRaw);
const todayStyles = rawAsset(todayStylesRaw);

function rawAsset(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source;
}

describe("workspace UX declutter happy path", () => {
  it("puts one real task before coach detail and learning assets before optional profile data", () => {
    const taskBody = todayTemplate.indexOf('class="primary-task__body"');
    const coachDetail = todayTemplate.indexOf('class="anan-insight"');
    const assets = meTemplate.indexOf("学习资产");
    const archive = meTemplate.indexOf("备考档案");

    expect(todayTemplate).toContain("{{openingView.weatherCompactLabel}}");
    expect(todayTemplate).toContain('class="header-weather"');
    expect(taskBody).toBeGreaterThan(-1);
    expect(taskBody).toBeLessThan(coachDetail);
    expect(todayTemplate).toContain("开始第一项任务");
    expect(todayStyles).toContain("background: var(--ink)");
    expect(assets).toBeGreaterThan(-1);
    expect(assets).toBeLessThan(archive);
    expect(meTemplate).toContain("不影响开始学习，需要核验岗位时再补。");
  });
});
