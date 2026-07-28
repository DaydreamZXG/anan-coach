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
import mockTemplate from "../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import mockStylesRaw from "../miniprogram/pages/mock-exam/index.wxss?raw";
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

const responsiveStyles = normalizeRpxCalc(responsiveStylesRaw);
const taskShellStyles = normalizeRpxCalc(taskShellStylesRaw);
const practiceStyles = normalizeRpxCalc(practiceStylesRaw);
const mockStyles = normalizeRpxCalc(mockStylesRaw);
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
    // 题目栏 class 尾随快滑切题的一次性转场类，因此只锚定前缀。
    expect(mockTemplate).toContain('class="mock-question-pane ');
    expect(mockTemplate).toContain('class="mock-material-pane"');
    expect(mockStyles).toContain("grid-template-columns: minmax(360px, 0.95fr)");
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
    expect(loginStyles).toContain("max-width: 1040px");
    expect(loginStyles).toContain("grid-template-columns: minmax(0, 0.9fr)");
    expect(loginStyles).toContain("margin: 0 auto");
  });
});
