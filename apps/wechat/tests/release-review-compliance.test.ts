import { describe, expect, it } from "vitest";

import appConfig from "../miniprogram/app.json";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import termsTemplate from "../miniprogram/pages/terms/index.wxml?raw";

describe("WeChat review compliance surface", () => {
  it("declares every directly requested runtime permission with a user-facing purpose", () => {
    expect(appConfig.permission["scope.userLocation"].desc).toContain("天气");
  });

  it("publishes a readable agreement without inventing a payment offer", () => {
    expect(appConfig.pages).toContain("pages/terms/index");
    expect(termsTemplate).toContain("岸岸用户协议");
    expect(termsTemplate).toContain("不替代招录机关公告");
    expect(termsTemplate).toContain("当前版本不提供购买入口");
    expect(termsTemplate).toContain("隐私保护指引");
    expect(termsTemplate).not.toContain("保证提分");
    expect(termsTemplate).not.toContain("保证上岸");
  });
});
