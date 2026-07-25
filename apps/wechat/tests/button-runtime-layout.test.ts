import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import appStyles from "../miniprogram/app.wxss?raw";
// @ts-expect-error Vite exposes mini-program component styles through its raw asset loader.
import uiButtonStyles from "../miniprogram/design-system/primitives/ui-button/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import dayAdjustmentTemplate from "../miniprogram/pages/day-adjustment/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import dayAdjustmentStyles from "../miniprogram/pages/day-adjustment/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import dailySettlementStyles from "../miniprogram/pages/daily-settlement/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import captureStyles from "../miniprogram/pages/capture-import/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayMaterialStyles from "../miniprogram/pages/essay-material/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayReviewStyles from "../miniprogram/pages/essay-review/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayTaskStyles from "../miniprogram/pages/essay-task/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import meStyles from "../miniprogram/pages/me/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingStyles from "../miniprogram/pages/onboarding/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingAbilityStyles from "../miniprogram/pages/onboarding/ability-baseline/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingEntryStyles from "../miniprogram/pages/onboarding/entry/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingPositionStyles from "../miniprogram/pages/onboarding/position-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingPreparationStyles from "../miniprogram/pages/onboarding/preparation-experience/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingResultStyles from "../miniprogram/pages/onboarding/result/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingStudyStyles from "../miniprogram/pages/onboarding/study-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import positionsStyles from "../miniprogram/features/positions/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import reflectionStyles from "../miniprogram/pages/reflection/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import weeklyPlanStyles from "../miniprogram/pages/weekly-plan/index.wxss?raw";

describe("WeChat runtime button layout contracts", () => {
  it("neutralizes the native fixed button width globally", () => {
    expect(appStyles).toMatch(/button\s*\{[\s\S]*?width:\s*auto;/u);
    expect(appStyles).toMatch(
      /button:not\(\[size="mini"\]\)\s*\{[\s\S]*?width:\s*auto;[\s\S]*?margin-left:\s*0;/u,
    );
  });

  it("lets the shared block button keep its full-width contract", () => {
    expect(uiButtonStyles).toContain("button.ui-button.ui-button--block");
    expect(uiButtonStyles).toMatch(
      /button\.ui-button\.ui-button--block\s*\{\s*width:\s*100%;\s*\}/u,
    );
  });

  it("keeps zero-duration adjustment copy direct and the zero-task card actionable", () => {
    expect(dayAdjustmentTemplate).toContain(
      "currentDurationLabel === '今天不安排' ? currentDurationLabel : '共 ' + currentDurationLabel",
    );
    expect(dayAdjustmentTemplate).toContain(
      "currentTaskCount > 0 ? currentTaskCount + ' 项任务' : '暂无任务'",
    );
    expect(dayAdjustmentTemplate).toContain("当前没有待完成任务；修改可用时间后");
    expect(dayAdjustmentTemplate).not.toContain("共 {{currentDurationLabel}}");
  });

  it("uses type-qualified width contracts where native button layout must not win", () => {
    const requiredMarkers: ReadonlyArray<readonly [string, string]> = [
      [dayAdjustmentStyles, "button.stepper-button"],
      [dayAdjustmentStyles, "button.time-preset"],
      [meStyles, "button.coach-setting"],
      [meStyles, "button.asset-item"],
      [meStyles, "button.service-row"],
      [weeklyPlanStyles, "button.week-primary"],
      [dailySettlementStyles, "button.settlement-primary"],
      [captureStyles, "button.camera-shutter"],
      [essayTaskStyles, "button.submit-button"],
      [essayMaterialStyles, "button.material-return"],
      [essayReviewStyles, "button.review-anan"],
      [reflectionStyles, "button.tag-chip"],
      [positionsStyles, "button.comparison-dock__action"],
      [onboardingStyles, "button.eligibility-primary"],
      [onboardingEntryStyles, "button.entry-action"],
      [onboardingPreparationStyles, "button.status-card"],
      [onboardingPositionStyles, "button.region-chip"],
      [onboardingStudyStyles, "button.time-step"],
      [onboardingAbilityStyles, "button.baseline-primary"],
      [onboardingResultStyles, "button.result-primary"],
    ];

    for (const [source, marker] of requiredMarkers) {
      expect(source).toContain(marker);
    }
  });

  it("allows the learning asset grid to wrap without cropping a fifth item", () => {
    expect(meStyles).toMatch(/\.asset-grid\s*\{[^}]*flex-wrap:\s*wrap;/u);
    expect(meStyles).toContain("flex: 0 0 calc((100% - 48rpx) / 4)");
  });
});
