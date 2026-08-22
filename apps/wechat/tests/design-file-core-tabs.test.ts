import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import ananTemplate from "../miniprogram/pages/anan/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import ananStylesRaw from "../miniprogram/pages/anan/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import profileTemplate from "../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import profileStylesRaw from "../miniprogram/pages/me/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import tabBarStylesRaw from "../miniprogram/custom-tab-bar/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import tabBarTemplate from "../miniprogram/custom-tab-bar/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import tabBarLogic from "../miniprogram/custom-tab-bar/index.ts?raw";
// @ts-expect-error Vite exposes mini-program page logic through its raw asset loader.
import profileLogic from "../miniprogram/pages/me/index.ts?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import componentHostTokensRaw from "../miniprogram/design-system/tokens/component-host.tokens.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import designFileTokensRaw from "../miniprogram/design-system/tokens/design-file.tokens.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import semanticTokensRaw from "../miniprogram/design-system/tokens/index.wxss?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const ananStyles = normalizeRpxCalc(ananStylesRaw);
const profileStyles = normalizeRpxCalc(profileStylesRaw);
const tabBarStyles = normalizeRpxCalc(tabBarStylesRaw);
const componentHostTokens = normalizeRpxCalc(componentHostTokensRaw);
const designFileTokens = normalizeRpxCalc(designFileTokensRaw);
const semanticTokens = normalizeRpxCalc(semanticTokensRaw);

function countMatches(source: unknown, pattern: RegExp): number {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.match(pattern)?.length ?? 0;
}

function tokenValue(source: unknown, token: string): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  const match = source.match(new RegExp(`${escaped}:\\s*([^;]+);`, "u"));
  const value = match?.[1];
  if (!value) throw new Error(`Missing design token ${token}.`);
  return value.trim();
}

describe("Design File core tab composition", () => {
  it("keeps the profile archive on the approved two-column, three-row grid", () => {
    expect(countMatches(profileTemplate, /class="archive-item"/gu)).toBe(6);
    expect(profileTemplate).toContain("工作经历");
    expect(profileTemplate).toContain("服务基层项目经历");
    expect(profileTemplate).not.toContain('class="archive-item archive-item--wide"');
    expect(profileStyles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(profileStyles).toContain("row-gap: 40rpx");
    expect(profileStyles).toContain("margin: 16rpx 0 16rpx");
    expect(profileStyles).toContain("min-height: 112rpx");
    expect(profileStyles).toContain("gap: 8rpx");
  });

  it("keeps the Anan first viewport aligned to the Design File control console", () => {
    expect(ananTemplate).toContain('<safe-top gap="4" />');
    expect(ananTemplate).toContain('src="/assets/icons/anan/brain-outline-white.svg"');
    expect(countMatches(ananTemplate, /class="suggestion-pill"/gu)).toBe(5);
    expect(ananTemplate).toContain('class="composer-surface"');
    expect(ananTemplate).toContain('src="/assets/icons/anan/send-arrow-white.svg"');
    expect(ananStyles).toContain("gap: 40rpx");
    expect(ananStyles).toContain("min-height: 90rpx");
    expect(ananStyles).toContain("min-height: 112rpx");
    expect(ananStyles).toContain("bottom: max(180rpx, calc(116rpx + env(safe-area-inset-bottom)))");
    expect(ananStyles).toMatch(
      /\.coach-core__copy\s*\{[^}]*overflow-wrap:\s*anywhere;[^}]*white-space:\s*normal;/su,
    );
    expect(ananStyles).not.toMatch(/\.coach-core__copy\s*\{[^}]*white-space:\s*nowrap;/su);
  });

  it("reserves the system safe area instead of adding it twice to the tab bar", () => {
    expect(tabBarStyles).toContain('@import "../design-system/tokens/component-host.tokens.wxss"');
    expect(tabBarStyles.replace(/\s+/gu, " ")).toContain(
      "padding: 26rpx 48rpx max(64rpx, env(safe-area-inset-bottom))",
    );
    expect(tabBarStyles).not.toContain("calc(64rpx + env(safe-area-inset-bottom))");
    expect(tabBarStyles).toContain("line-height: 30rpx");
    expect(tabBarTemplate).toContain('wx:if="{{!hidden}}" class="tab-bar component-token-host"');
  });

  it("hides the custom tab bar while profile sheets are open", () => {
    expect(tabBarLogic).toContain("hidden: false");
    expect(tabBarLogic).toContain("setHidden(hidden: boolean)");
    expect(tabBarTemplate).toContain('wx:if="{{!hidden}}"');
    expect(profileLogic).toContain("this.setTabBarHidden(true)");
    expect(profileLogic).toContain("profileEditorOpen || this.data.wechatChannelOpen");
  });

  it("keeps isolated tab-bar host tokens synchronized with the Design File", () => {
    for (const token of [
      "--figma-color-canvas",
      "--figma-color-surface",
      "--figma-color-surface-subtle",
      "--figma-color-ink",
      "--figma-color-faint",
      "--figma-color-line",
      "--figma-font-tab",
    ]) {
      expect(tokenValue(componentHostTokens, token)).toBe(tokenValue(designFileTokens, token));
    }
    expect(tokenValue(componentHostTokens, "--z-sticky")).toBe(
      tokenValue(semanticTokens, "--z-sticky"),
    );
  });
});
