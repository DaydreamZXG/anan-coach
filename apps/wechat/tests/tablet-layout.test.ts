import { describe, expect, it } from "vitest";

import appConfig from "../miniprogram/app.json";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import responsiveStyles from "../miniprogram/design-system/responsive/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import taskShellStyles from "../miniprogram/task-runtime/task-shell/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import practiceStyles from "../miniprogram/pages/practice/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import mockTemplate from "../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import mockStyles from "../miniprogram/pages/mock-exam/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import essayTemplate from "../miniprogram/pages/essay-task/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayStyles from "../miniprogram/pages/essay-task/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import essayWorkbenchStyles from "../miniprogram/features/essay-workbench/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import positionStyles from "../miniprogram/features/positions/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import brainStyles from "../miniprogram/features/learning-brain/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import reportingStyles from "../miniprogram/features/reporting/page-shell.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import captureStyles from "../miniprogram/pages/capture-import/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import loginStyles from "../miniprogram/pages/login/index.wxss?raw";

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
    expect(practiceStyles).toContain("position: sticky");

    expect(mockTemplate).toContain('class="mock-workspace ');
    expect(mockTemplate).toContain('class="mock-question-pane"');
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
    expect(loginStyles).toContain("max-width: 560px");
    expect(loginStyles).toContain("margin: 0 auto");
  });
});
