import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import meTemplate from "../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import supportSource from "../miniprogram/account/support/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import supportTemplate from "../miniprogram/account/support/index.wxml?raw";

describe("微信客服入口", () => {
  it("从我的页面进入二维码客服页，不再调用微信客服会话", () => {
    expect(meTemplate).toContain('bindtap="openSupport"');
    expect(meTemplate).not.toContain('open-type="contact"');
  });

  it("展示本地客服二维码并允许长按识别和点击放大", () => {
    expect(supportSource).toContain("/assets/wechat-support-qr.jpg");
    expect(supportTemplate).toContain("show-menu-by-longpress");
    expect(supportTemplate).toContain('bindtap="previewGroupQr"');
    expect(supportTemplate).not.toContain('open-type="contact"');
  });
});
