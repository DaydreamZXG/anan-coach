import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import todayLogic from "../miniprogram/pages/today/index.ts?raw";

describe("today information architecture", () => {
  it("keeps xingce and essay visible as equal daily tracks", () => {
    expect(todayTemplate).toContain('wx:for="{{view.trackViews}}"');
    expect(todayTemplate).toContain("行测与申论每天都保留状态");
    expect(todayTemplate).toContain('bindtap="openTrack"');
    expect(todayLogic).toContain('if (track === "xingce")');
    expect(todayLogic).toContain('if (track === "essay")');
  });

  it("does not present settlement as the primary action before any activity", () => {
    expect(todayTemplate).toContain("view.hasExecutionActivity");
    expect(todayTemplate).toContain("settlement-button--quiet");
    expect(todayTemplate).toContain("'记录今天'");
  });

  it("renders state-specific recovery and completion actions", () => {
    expect(todayTemplate).toContain("view.dayState === 'recovery'");
    expect(todayTemplate).toContain("查看到期复盘");
    expect(todayTemplate).toContain("我想加练");
    expect(todayTemplate).toContain("view.dayState === 'complete'");
  });

  it("keeps the existing full mock available outside the scheduled mock day", () => {
    expect(todayTemplate).toContain("整卷模考");
    expect(todayTemplate).toContain("135 题 · 120 分钟");
    expect(todayTemplate).toContain("进入模考 →");
    expect(todayTemplate).toContain('bindtap="openMockExam"');
    expect(todayLogic).toContain("routes.mockExam");
  });
});
