import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import meTemplateRaw from "../../../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplateRaw from "../../../miniprogram/pages/today/index.wxml?raw";

const meTemplate = rawAsset(meTemplateRaw);
const todayTemplate = rawAsset(todayTemplateRaw);

function rawAsset(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source;
}

describe("workspace UX declutter failure isolation", () => {
  it("keeps weather and qualification failures from becoming workspace gates", () => {
    expect(todayTemplate).toContain("今天的计划没加载出来");
    expect(todayTemplate).not.toContain("openingStatus === 'error'");
    expect(todayTemplate).not.toContain("每日开场没有加载成功");
    expect(todayTemplate).not.toContain("{{openingError}}");
    expect(meTemplate).toContain("报考资料暂时没同步好");
    expect(meTemplate).toContain("学习资产");
    expect(meTemplate.indexOf("学习资产")).toBeLessThan(meTemplate.indexOf("报考资料暂时没同步好"));
  });
});
