import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/login/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import styles from "../miniprogram/pages/login/index.wxss?raw";

describe("stage 9 login composition", () => {
  it("renders the approved 岸岸 value proposition and full-size companion", () => {
    expect(template).toContain("你的国考 AI 备考搭子");
    expect(template).toContain("把分差");
    expect(template).toContain("今天的行动");
    expect(template).toContain("岗位定位");
    expect(template).toContain("每日计划");
    expect(template).toContain("证据复盘");
    expect(template).toContain('size="hero-240"');
    expect(template).toContain('<button\n      class="wechat-button ');
    expect(template).toContain("wechat-button--disabled");
    expect(template).toContain('disabled="{{submitting || restoring}}"');
    expect(template).not.toContain("<ui-button");
    expect(template).toContain("仅收集建档和学习分析所需信息");
    expect(template).toContain("可导出，也可删除");
    expect(template).not.toContain("它不催你多刷题");
    expect(template).not.toContain("promise-row");
  });

  it("keeps the AI glow surface and clear login action hierarchy", () => {
    expect(styles).toContain("radial-gradient");
    expect(styles).toContain("var(--color-canvas)");
    expect(styles).toContain("var(--gradient-ai)");
    expect(styles).toContain(".wechat-button");
    expect(styles).toContain("width: 638rpx !important");
    expect(styles).toContain("rgba(11, 167, 144");
    expect(styles).not.toContain("rgba(240, 195, 140");
  });
});
