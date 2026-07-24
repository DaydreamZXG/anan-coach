import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import ananTemplate from "../miniprogram/pages/anan/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import ananStyles from "../miniprogram/pages/anan/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import profileTemplate from "../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import profileStyles from "../miniprogram/pages/me/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import tabBarStyles from "../miniprogram/custom-tab-bar/index.wxss?raw";

describe("Design File core tab composition", () => {
  it("keeps the profile archive on the approved two-column, three-row grid", () => {
    expect(profileTemplate.match(/class="archive-item"/gu)).toHaveLength(6);
    expect(profileTemplate).toContain("工作经历");
    expect(profileTemplate).toContain("服务基层项目经历");
    expect(profileTemplate).not.toContain('class="archive-item archive-item--wide"');
    expect(profileStyles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(profileStyles).toContain("row-gap: 40rpx");
    expect(profileStyles).toContain("margin: 32rpx 0 16rpx");
    expect(profileStyles).toContain("min-height: 112rpx");
    expect(profileStyles).toContain("gap: 8rpx");
  });

  it("keeps the Anan first viewport aligned to the Design File control console", () => {
    expect(ananTemplate).toContain('<safe-top mode="status" gap="16" />');
    expect(ananTemplate).toContain(
      'src="/assets/icons/anan/logo-design-file.png"',
    );
    expect(ananTemplate.match(/class="suggestion-pill"/gu)).toHaveLength(5);
    expect(ananTemplate).toContain('class="composer-surface"');
    expect(ananTemplate).toContain(
      'src="/assets/icons/anan/send-arrow-white.svg"',
    );
    expect(ananStyles).toContain("gap: 40rpx");
    expect(ananStyles).toContain("min-height: 90rpx");
    expect(ananStyles).toContain("min-height: 112rpx");
    expect(ananStyles).toContain(
      "bottom: max(180rpx, calc(116rpx + env(safe-area-inset-bottom)))",
    );
  });

  it("reserves the system safe area instead of adding it twice to the tab bar", () => {
    expect(tabBarStyles).toContain(
      "padding: 26rpx 48rpx max(64rpx, env(safe-area-inset-bottom))",
    );
    expect(tabBarStyles).not.toContain(
      "calc(64rpx + env(safe-area-inset-bottom))",
    );
    expect(tabBarStyles).toContain("line-height: 30rpx");
  });
});
