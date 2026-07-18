import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/login/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import styles from "../miniprogram/pages/login/index.wxss?raw";

describe("stage 9 login composition", () => {
  it("renders the approved 岸岸 value proposition and full-size companion", () => {
    expect(template).toContain("基于真实题与岗位数据的 AI 备考伙伴");
    expect(template).toContain("把分差");
    expect(template).toContain("今天的行动");
    expect(template).toContain('size="hero-240"');
    expect(template).toContain('<button\n      class="wechat-button ');
    expect(template).toContain("wechat-button--disabled");
    expect(template).toContain('disabled="{{submitting || restoring}}"');
    expect(template).not.toContain("<ui-button");
    expect(template).toContain("数据加密存储，可随时导出与删除");
    expect(template).not.toContain("它不催你多刷题");
    expect(template).not.toContain("promise-row");
  });

  it("keeps the approved warm-mint surface and WeChat action hierarchy", () => {
    expect(styles).toContain("radial-gradient");
    expect(styles).toContain("#009f8d");
    expect(styles).toContain(".wechat-button");
    expect(styles).toContain("width: 638rpx !important");
    expect(styles).not.toContain("rgba(82, 104, 255");
  });
});
