import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import taskTemplateRaw from "../../../miniprogram/pages/essay-task/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import taskSourceRaw from "../../../miniprogram/pages/essay-task/index.ts?raw";

describe("essay task closure 034 recovery path", () => {
  it("leaves an enabled return action when automatic navigation fails", () => {
    const template = String(taskTemplateRaw);
    const source = String(taskSourceRaw);

    expect(template).toContain('wx:if="{{completion}}"');
    expect(template).toContain('bindtap="returnTodayAfterCompletion"');
    expect(template).toContain("再试一次：回到今日");
    expect(source).toContain("completionNavigationInFlight");
    expect(source).toContain("completionAutoReturnFailed: true");
  });
});
