import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import primitiveStylesRaw from "../miniprogram/design-system/primitives/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import safeTopLogic from "../miniprogram/components/safe-top/index.ts?raw";
// @ts-expect-error Vite exposes mini-program component logic through its raw asset loader.
import workbenchNavLogic from "../miniprogram/plans/shared/components/workbench-nav/index.ts?raw";
// @ts-expect-error Vite exposes mini-program component templates through its raw asset loader.
import workbenchNavTemplate from "../miniprogram/plans/shared/components/workbench-nav/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import workbenchNavStylesRaw from "../miniprogram/plans/shared/components/workbench-nav/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingShellStylesRaw from "../miniprogram/features/onboarding/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import tabBarStylesRaw from "../miniprogram/custom-tab-bar/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import profileArchiveTemplate from "../miniprogram/pages/profile/archive/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import privateKnowledgeTemplate from "../miniprogram/pages/private-knowledge/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import privateKnowledgeDetailTemplate from "../miniprogram/pages/private-knowledge/detail/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import todayTemplate from "../miniprogram/pages/today/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import abilityTemplate from "../miniprogram/pages/ability/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import ananTemplate from "../miniprogram/pages/anan/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import meTemplate from "../miniprogram/pages/me/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import abilityBaselineTemplate from "../miniprogram/pages/onboarding/ability-baseline/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import qualificationTemplate from "../miniprogram/pages/onboarding/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import positionPreferenceTemplate from "../miniprogram/pages/onboarding/position-preference/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import preparationExperienceTemplate from "../miniprogram/pages/onboarding/preparation-experience/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import studyPreferenceTemplate from "../miniprogram/pages/onboarding/study-preference/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program page templates through its raw asset loader.
import reflectionTemplate from "../miniprogram/pages/reflection/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import reflectionStylesRaw from "../miniprogram/pages/reflection/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program source through its raw asset loader.
import reflectionSource from "../miniprogram/pages/reflection/index.ts?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const primitiveStyles = normalizeRpxCalc(primitiveStylesRaw);
const workbenchNavStyles = normalizeRpxCalc(workbenchNavStylesRaw);
const onboardingShellStyles = normalizeRpxCalc(onboardingShellStylesRaw);
const tabBarStyles = normalizeRpxCalc(tabBarStylesRaw);
const reflectionStyles = normalizeRpxCalc(reflectionStylesRaw);

describe("runtime layout foundation", () => {
  it("keeps generic section layout out of the global namespace", () => {
    expect(primitiveStyles).toContain(".ui-section-heading");
    expect(primitiveStyles).not.toMatch(/(?:^|\n)\.section-heading\s*\{/u);
  });

  it("reserves real WeChat capsule geometry in shared navigation", () => {
    expect(safeTopLogic).toContain("任何标题、返回或关闭控件都必须使用 menu");
    expect(workbenchNavLogic).toContain("measureSystemChrome(8)");
    expect(workbenchNavLogic).toContain("capsuleReserve");
    expect(workbenchNavTemplate).toContain("workbench-nav--{{tone}}");
    expect(workbenchNavTemplate).toContain("grid-template-columns: {{capsuleReserve}}px");
    expect(workbenchNavStyles).toContain("grid-column: 1");
    expect(workbenchNavStyles).toContain("grid-column: 2");
    expect(workbenchNavStyles).toContain(".workbench-nav--dark");
    expect(workbenchNavStyles).toContain("background: var(--figma-color-canvas)");

    for (const template of [todayTemplate, abilityTemplate, ananTemplate, meTemplate]) {
      expect(template).toContain('<safe-top gap="4" />');
      expect(template).not.toContain('mode="status"');
    }
  });

  it("keeps trajectory replay metadata outside the WeChat capsule", () => {
    expect(reflectionSource).toContain("measureSystemChrome(8)");
    expect(reflectionTemplate).toContain("--replay-capsule-reserve: {{replayCapsuleReserve}}px");
    expect(reflectionStyles).toContain("var(--replay-capsule-reserve, 96px)");
    expect(reflectionStyles).toContain("var(--replay-navigation-height, 44px)");
    expect(reflectionStyles).not.toContain(
      ".replay-chip {\n  position: absolute;\n  top: calc(env(safe-area-inset-top)",
    );
  });

  it("keeps all five onboarding steps in the capsule row and moves save to the action dock", () => {
    for (const template of [
      preparationExperienceTemplate,
      qualificationTemplate,
      positionPreferenceTemplate,
      studyPreferenceTemplate,
      abilityBaselineTemplate,
    ]) {
      expect(template).toContain("<workbench-nav");
      expect(template).toContain('back-behavior="emit"');
      expect(template).toContain('bind:back="back"');
      expect(template).toContain('class="onboarding-save-action"');
      expect(template).not.toContain("<safe-top");
      expect(template).not.toContain('class="nav-save');
    }

    expect(workbenchNavLogic).toContain('this.data.backBehavior === "emit"');
    expect(workbenchNavLogic).toContain('this.triggerEvent("back")');
    expect(workbenchNavTemplate).toContain('class="workbench-nav__back-icon"');
    expect(onboardingShellStyles).toContain(
      "padding-bottom: calc(352rpx + env(safe-area-inset-bottom))",
    );
    expect(onboardingShellStyles).toContain("button.onboarding-save-action");
  });

  it("keeps the tab bar opaque so page content cannot bleed through", () => {
    expect(tabBarStyles).toContain("background: var(--figma-color-surface)");
    expect(tabBarStyles).not.toMatch(/background:\s*rgba\(/u);
  });

  it("declares archive icons as static package dependencies", () => {
    expect(profileArchiveTemplate).not.toContain('src="{{item.iconSrc}}"');
    expect(profileArchiveTemplate).not.toContain("archive-{{item.icon}}");
    for (const icon of ["identity", "qualification", "position", "time", "ability"]) {
      expect(profileArchiveTemplate).toContain(`src="/assets/icons/profile/archive-${icon}.svg"`);
    }
  });

  it("keeps the private knowledge workspace free of generated bottom tabs", () => {
    for (const template of [privateKnowledgeTemplate, privateKnowledgeDetailTemplate]) {
      expect(template).not.toContain("<library-nav");
      expect(template).not.toContain("library-bottom-spacer");
      expect(template).not.toContain("detail-bottom-spacer");
    }
  });
});
