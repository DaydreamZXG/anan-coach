import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import appStylesRaw from "../miniprogram/app.wxss?raw";
// @ts-expect-error Vite exposes mini-program component styles through its raw asset loader.
import uiButtonStylesRaw from "../miniprogram/design-system/primitives/ui-button/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import dayAdjustmentTemplate from "../miniprogram/plans/day-adjustment/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import dayAdjustmentStylesRaw from "../miniprogram/plans/day-adjustment/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import dailySettlementStylesRaw from "../miniprogram/plans/daily-settlement/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import captureStylesRaw from "../miniprogram/pages/capture-import/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayMaterialStylesRaw from "../miniprogram/essay/essay-material/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayReviewStylesRaw from "../miniprogram/essay/essay-review/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayTaskStylesRaw from "../miniprogram/pages/essay-task/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import meStylesRaw from "../miniprogram/pages/me/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingStylesRaw from "../miniprogram/pages/onboarding/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingAbilityStylesRaw from "../miniprogram/pages/onboarding/ability-baseline/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingEntryStylesRaw from "../miniprogram/pages/onboarding/entry/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingPositionStylesRaw from "../miniprogram/pages/onboarding/position-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingPreparationStylesRaw from "../miniprogram/pages/onboarding/preparation-experience/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingResultStylesRaw from "../miniprogram/pages/onboarding/result/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import onboardingStudyStylesRaw from "../miniprogram/pages/onboarding/study-preference/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import positionsStylesRaw from "../miniprogram/features/positions/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import reflectionStylesRaw from "../miniprogram/pages/reflection/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import weeklyPlanStylesRaw from "../miniprogram/plans/weekly-plan/index.wxss?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

const appStyles = normalizeRpxCalc(appStylesRaw);
const uiButtonStyles = normalizeRpxCalc(uiButtonStylesRaw);
const dayAdjustmentStyles = normalizeRpxCalc(dayAdjustmentStylesRaw);
const dailySettlementStyles = normalizeRpxCalc(dailySettlementStylesRaw);
const captureStyles = normalizeRpxCalc(captureStylesRaw);
const essayMaterialStyles = normalizeRpxCalc(essayMaterialStylesRaw);
const essayReviewStyles = normalizeRpxCalc(essayReviewStylesRaw);
const essayTaskStyles = normalizeRpxCalc(essayTaskStylesRaw);
const meStyles = normalizeRpxCalc(meStylesRaw);
const onboardingStyles = normalizeRpxCalc(onboardingStylesRaw);
const onboardingAbilityStyles = normalizeRpxCalc(onboardingAbilityStylesRaw);
const onboardingEntryStyles = normalizeRpxCalc(onboardingEntryStylesRaw);
const onboardingPositionStyles = normalizeRpxCalc(onboardingPositionStylesRaw);
const onboardingPreparationStyles = normalizeRpxCalc(onboardingPreparationStylesRaw);
const onboardingResultStyles = normalizeRpxCalc(onboardingResultStylesRaw);
const onboardingStudyStyles = normalizeRpxCalc(onboardingStudyStylesRaw);
const positionsStyles = normalizeRpxCalc(positionsStylesRaw);
const reflectionStyles = normalizeRpxCalc(reflectionStylesRaw);
const weeklyPlanStyles = normalizeRpxCalc(weeklyPlanStylesRaw);

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
