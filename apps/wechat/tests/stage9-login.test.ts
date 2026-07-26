import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/login/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import stylesRaw from "../miniprogram/pages/login/index.wxss?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const styles = normalizeRpxCalc(stylesRaw);

describe("stage 9 login composition", () => {
  it("renders the approved quiet AI coach identity and a single clear login action", () => {
    expect(template).toContain("岸岸备考");
    expect(template).toContain("基于认知科学的 AI 备考教练");
    expect(template).toContain("为你规划最快的上岸路径");
    expect(template).toContain('class="brain-logo"');
    expect(template).toContain('src="/assets/icons/anan/brain-outline-ink.svg"');
    expect(template).toContain('src="/assets/icons/anan/arrow-right-white.svg"');
    expect(template).toContain('src="/assets/icons/anan/check-faint.svg"');
    expect(template).not.toContain("brain-lobe");
    expect(template).not.toContain("brain-stem");
    expect(template).toContain('class="wechat-button {{submitting || restoring');
    expect(template).toContain("wechat-button--disabled");
    expect(template).toContain('disabled="{{submitting || restoring}}"');
    expect(template).not.toContain("<ui-button");
    expect(template).toContain("登录即代表同意用户协议与隐私政策");
  });

  it("uses the Design File asset and keeps a clear login action hierarchy", () => {
    expect(styles).toContain(".login-orb");
    expect(styles).toContain("var(--figma-gradient-login-halo-top)");
    expect(styles).toContain("var(--figma-gradient-login-halo-bottom)");
    expect(styles).toContain(".brain-logo-shell");
    expect(styles).toContain(".brain-logo-tint");
    expect(styles).toContain(".brain-logo");
    expect(styles).not.toContain(".brain-tile");
    expect(styles).not.toContain(".brain-mark");
    expect(styles).not.toContain(".brain-lobe");
    expect(styles).not.toContain(".brain-stem");
    expect(styles).toContain(".wechat-button");
    expect(styles).toContain("width: 100% !important");
    expect(styles).toContain("background: var(--figma-color-ink)");
    expect(styles).not.toContain("rgba(240, 195, 140");
  });
});
