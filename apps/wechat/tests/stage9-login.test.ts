import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/login/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import stylesRaw from "../miniprogram/pages/login/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import source from "../miniprogram/pages/login/index.ts?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const styles = normalizeRpxCalc(stylesRaw);

describe("岸岸静默身份入口构成", () => {
  it("自动进入岸岸，不展示登录、游客或协议门槛", () => {
    expect(template).toContain("正在进入岸岸");
    expect(template).toContain("正在同步你的学习档案");
    expect(template).toContain("暂时没能进入岸岸");
    expect(template).toContain('class="brain-logo"');
    expect(template).toContain('src="/assets/icons/anan/brain-outline-ink.svg"');
    expect(template).toContain('src="/assets/icons/anan/arrow-right-white.svg"');
    expect(template).not.toContain("brain-lobe");
    expect(template).not.toContain("brain-stem");
    expect(template).toContain('class="entry-progress');
    expect(template).toContain('class="wechat-button {{submitting || restoring');
    expect(template).toContain("wechat-button--disabled");
    expect(template).toContain('disabled="{{submitting || restoring}}"');
    expect(template).toContain('bindtap="retry"');
    expect(template).toContain("重新进入岸岸");
    expect(template).not.toContain("<ui-button");
    expect(template).not.toContain("微信一键登录");
    expect(template).not.toContain("游客");
    expect(template).not.toContain("legalAccepted");
    expect(template).not.toContain("用户协议");
    expect(template).not.toContain("隐私保护指引");
    expect(source).toContain("bootstrapSilentWechatSession");
    expect(source).toContain("SILENT_WECHAT_LEGAL_ACCEPTED");
    expect(source).not.toContain("wx.openPrivacyContract");
    expect(source).not.toContain("toggleLegalConsent");
  });

  it("保留品牌外壳、失败恢复按钮和 600px 响应式布局", () => {
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
    expect(styles).toContain(".entry-progress");
    expect(styles).toContain("@keyframes entry-pulse");
    expect(styles).toContain(".wechat-button");
    expect(styles).not.toContain("button.legal-link");
    expect(styles).toContain("width: 100% !important");
    expect(styles).toContain("background: var(--figma-color-ink)");
    expect(styles).toContain("@media (min-width: 600px)");
    expect(styles).not.toContain("rgba(240, 195, 140");
  });
});
