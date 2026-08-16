import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplateRaw from "../../../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import todayStylesRaw from "../../../miniprogram/pages/today/index.wxss?raw";

const todayTemplate = rawAsset(todayTemplateRaw);
const todayStyles = rawAsset(todayStylesRaw);

function rawAsset(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source;
}

describe("workspace UX declutter replay", () => {
  it("cannot recreate the removed opening card or duplicate the focused task body", () => {
    expect(todayTemplate).not.toContain('class="opening-card"');
    expect(todayTemplate).not.toContain("今日节奏");
    expect(todayTemplate).not.toContain('bindtap="startDailyOpening"');
    expect(todayTemplate.match(/class="primary-task__body"/gu)).toHaveLength(1);
    expect(todayStyles).not.toContain(".opening-card");
    expect(todayStyles).not.toContain(".opening-failure");
  });
});
