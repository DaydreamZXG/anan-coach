import { describe, expect, it } from "vitest";

import appConfig from "../miniprogram/app.json";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import responsiveStylesRaw from "../miniprogram/design-system/responsive/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import taskShellStylesRaw from "../miniprogram/task-runtime/task-shell/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import practiceStylesRaw from "../miniprogram/pages/practice/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import favoritesTemplate from "../miniprogram/pages/xingce-mastery/favorites/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import favoritesStylesRaw from "../miniprogram/pages/xingce-mastery/favorites/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import mockTemplate from "../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import mockStylesRaw from "../miniprogram/pages/mock-exam/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import answerSheetStylesRaw from "../miniprogram/task-runtime/answer-sheet/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import essayTemplate from "../miniprogram/pages/essay-task/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayStylesRaw from "../miniprogram/pages/essay-task/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayWorkbenchStylesRaw from "../miniprogram/features/essay-workbench/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import positionStylesRaw from "../miniprogram/features/positions/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import brainStylesRaw from "../miniprogram/features/learning-brain/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import reportingStylesRaw from "../miniprogram/features/reporting/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import captureStylesRaw from "../miniprogram/pages/capture-import/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import loginStylesRaw from "../miniprogram/pages/login/index.wxss?raw";

function normalizeRpxCalc(source: unknown): string {
  if (typeof source !== "string") throw new Error("Raw mini-program asset must be text.");
  return source.replace(/calc\((-?\d+(?:\.\d+)?) \* var\(--rpx\)\)/gu, "$1rpx");
}

function ruleBodies(source: string, selector: string): string[] {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&");
  return Array.from(
    source.matchAll(new RegExp(`${escapedSelector}\\s*\\{([^}]+)\\}`, "gu")),
    (match) => match[1] ?? "",
  );
}

const responsiveStyles = normalizeRpxCalc(responsiveStylesRaw);
const taskShellStyles = normalizeRpxCalc(taskShellStylesRaw);
const practiceStyles = normalizeRpxCalc(practiceStylesRaw);
const favoritesStyles = normalizeRpxCalc(favoritesStylesRaw);
const mockStyles = normalizeRpxCalc(mockStylesRaw);
const answerSheetStyles = normalizeRpxCalc(answerSheetStylesRaw);
const essayStyles = normalizeRpxCalc(essayStylesRaw);
const essayWorkbenchStyles = normalizeRpxCalc(essayWorkbenchStylesRaw);
const positionStyles = normalizeRpxCalc(positionStylesRaw);
const brainStyles = normalizeRpxCalc(brainStylesRaw);
const reportingStyles = normalizeRpxCalc(reportingStylesRaw);
const captureStyles = normalizeRpxCalc(captureStylesRaw);
const loginStyles = normalizeRpxCalc(loginStylesRaw);

describe("tablet responsive layout contract", () => {
  it("supports portrait and landscape without creating a second application", () => {
    expect(appConfig.window.pageOrientation).toBe("auto");
    expect(responsiveStyles).toContain("@media (min-width: 768px)");
    expect(responsiveStyles).toContain("--layout-readable-max");
    expect(responsiveStyles).toContain("font-size: 16px");
    expect(taskShellStyles).toContain("max-width: var(--layout-workbench-max)");
    expect(taskShellStyles).toContain("@media (min-width: 1024px)");
  });

  it("turns xingce and mock answering into material-question workbenches", () => {
    expect(practiceTemplate).toContain('class="practice-workspace ');
    expect(practiceTemplate).toContain('class="practice-question-pane"');
    expect(practiceStyles).toContain("grid-template-columns: minmax(340px, 0.95fr)");
    expect(practiceTemplate).toContain(
      "footerAvailable=\"{{status === 'ready' && sessionState === 'answering' && view && view.current === view.total}}\"",
    );
    expect(practiceTemplate).toContain("左右滑动切题，点顶部题号可打开答题卡");
    expect(practiceTemplate).not.toContain("footer-previous");
    expect(practiceTemplate).not.toContain("? '下一题'");
    // Figma 2:2：材料与题目各自滚动，宽屏由等高双栏承载，不再依赖 sticky。
    expect(practiceStyles).toMatch(/\.material-reader-shell \{[^}]*height: 100%/);

    expect(mockTemplate).toContain('class="mock-workspace ');
    expect(mockTemplate).toContain('layoutVariant="xingce"');
    expect(mockTemplate).toContain('<scroll-view\n        class="mock-question-pane"');
    expect(mockTemplate).toContain('scroll-top="{{questionScrollTop}}"');
    expect(mockTemplate).toContain("<question-toolbar");
    expect(mockTemplate).toContain("<annotation-layer");
    expect(mockTemplate).toContain(
      "footerAvailable=\"{{status === 'ready' && current === total}}\"",
    );
    expect(mockTemplate).not.toContain("footer-previous");
    expect(mockTemplate).not.toContain(">下一题</button>");
    expect(mockTemplate).toContain('class="mock-material-pane"');
    expect(mockStyles).toContain("grid-template-columns: minmax(360px, 0.95fr)");
  });

  it("keeps the practice answer card and CTA inside phone and tablet viewports", () => {
    const [phoneRule, tabletRule] = ruleBodies(answerSheetStyles, ".answer-card__sheet");
    expect(practiceStyles).toContain("@media (min-width: 768px)");
    expect(phoneRule).toBeDefined();
    expect(tabletRule).toBeDefined();

    expect(phoneRule).toMatch(/\bright:\s*0;/u);
    expect(phoneRule).toMatch(/\bleft:\s*0;/u);
    expect(phoneRule).toMatch(/\bpadding:\s*16rpx 40rpx/u);
    expect(tabletRule).toMatch(/\bright:\s*50%;/u);
    expect(tabletRule).toMatch(/\bleft:\s*auto;/u);
    expect(tabletRule).toMatch(/\bwidth:\s*100%;/u);
    expect(tabletRule).toMatch(/\bmax-width:\s*560px;/u);
    expect(tabletRule).toMatch(/\btransform:\s*translateX\(50%\);/u);
    expect(tabletRule).toMatch(/\bpadding:\s*8px 20px/u);

    for (const viewportWidth of [320, 363, 390, 767]) {
      const horizontalPadding = (40 * viewportWidth) / 750;
      const sheetLeft = 0;
      const sheetRight = viewportWidth;
      const ctaLeft = sheetLeft + horizontalPadding;
      const ctaRight = sheetRight - horizontalPadding;

      expect(sheetLeft).toBeGreaterThanOrEqual(0);
      expect(sheetRight).toBeLessThanOrEqual(viewportWidth);
      expect(ctaLeft).toBeGreaterThanOrEqual(sheetLeft);
      expect(ctaRight).toBeLessThanOrEqual(sheetRight);
    }

    for (const viewportWidth of [768, 800, 1024]) {
      const sheetWidth = Math.min(viewportWidth, 560);
      const sheetLeft = (viewportWidth - sheetWidth) / 2;
      const sheetRight = sheetLeft + sheetWidth;
      const ctaLeft = sheetLeft + 20;
      const ctaRight = sheetRight - 20;

      expect(sheetLeft).toBeGreaterThanOrEqual(0);
      expect(sheetRight).toBeLessThanOrEqual(viewportWidth);
      expect(ctaLeft).toBeGreaterThanOrEqual(sheetLeft);
      expect(ctaRight).toBeLessThanOrEqual(sheetRight);
    }
  });

  it("keeps the favorites list readable in tablet portrait and landscape", () => {
    expect(favoritesTemplate).toContain('class="favorites-list"');
    expect(favoritesTemplate).toContain('class="favorite-card__cancel control-center"');
    expect(favoritesStyles).toContain("@media (min-width: 768px)");
    expect(favoritesStyles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(favoritesStyles).toContain("@media (min-width: 1024px)");
    expect(favoritesStyles).toContain("max-width: 920px");
    expect(favoritesStyles).toMatch(/\.favorite-card__cancel,[\s\S]*?min-height:\s*44px;/u);
  });

  it("keeps essay source context beside the canonical writing workflow", () => {
    expect(essayTemplate).toContain('class="essay-workbench-grid"');
    expect(essayTemplate).toContain('class="essay-context-pane"');
    expect(essayTemplate).toContain('class="essay-action-pane"');
    expect(essayStyles).toContain("grid-template-columns: minmax(360px, 0.9fr)");
    expect(essayStyles).toContain("max-height: calc(100vh - 180px)");
    expect(essayWorkbenchStyles).toContain("max-width: 1120px");
  });

  it("uses real pixel caps and multi-column position comparisons on tablet", () => {
    expect(positionStyles).toContain("max-width: 1180px");
    expect(positionStyles).toContain(".position-card-grid");
    expect(positionStyles).toContain("repeat(auto-fit, minmax(300px, 1fr))");
    expect(positionStyles).not.toContain("width: 760rpx");
  });

  it("covers the remaining page families instead of adapting only the workbenches", () => {
    expect(brainStyles).toContain("max-width: 1120px");
    expect(reportingStyles).toContain("grid-template-columns: repeat(2, minmax(0, 1fr))");
    expect(captureStyles).toContain("max-width: 900px");
    expect(captureStyles).toContain(".capture-page--camera");
    expect(captureStyles).toContain("width: min(72vw, 680px)");
    expect(captureStyles).toContain("max-height: 780px");
    expect(loginStyles).toContain("@media (min-width: 600px) and (min-height: 600px)");
    expect(loginStyles).toContain("max-width: 520px");
    expect(loginStyles).toContain("flex-direction: column");
    expect(loginStyles).toContain("justify-content: center");
    expect(loginStyles).toContain("max-width: 440px");
    expect(loginStyles).toContain("align-self: center");
    expect(loginStyles).not.toContain("grid-template-columns");
    expect(loginStyles).not.toContain("justify-self: end");
    expect(loginStyles).not.toContain("gap: 80px");
    expect(loginStyles).toContain("margin: 0 auto");
  });
});
