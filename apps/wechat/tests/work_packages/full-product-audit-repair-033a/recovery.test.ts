import { afterEach, describe, expect, it, vi } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import sharedMaterialSourceRaw from "../../../subpackage-shared-src/task-runtime/material-reader/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import sharedMaterialTemplateRaw from "../../../subpackage-shared-src/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceMaterialTemplateRaw from "../../../miniprogram/pages/practice/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import mockMaterialTemplateRaw from "../../../miniprogram/pages/mock-exam/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import reflectionMaterialTemplateRaw from "../../../miniprogram/pages/reflection/shared/task-runtime/material-reader/index.wxml?raw";
import { navigateWithImmediateFeedback } from "../../../miniprogram/platform/navigation-feedback";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("full product audit repair 033a recovery path", () => {
  it("keeps native feedback visible through page creation and always removes it", async () => {
    vi.useFakeTimers();
    const showLoading = vi.fn(() => Promise.resolve());
    const hideLoading = vi.fn(() => Promise.resolve());
    const navigateTo = vi.fn(() => Promise.resolve());
    vi.stubGlobal("wx", { showLoading, hideLoading, navigateTo });

    await navigateWithImmediateFeedback(
      { mode: "navigateTo", url: "/xingce/xingce-task-intro/index" },
      "正在打开任务",
    );
    expect(showLoading).toHaveBeenCalledWith({ title: "正在打开任务", mask: true });
    expect(navigateTo).toHaveBeenCalledWith({ url: "/xingce/xingce-task-intro/index" });
    expect(hideLoading).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(450);
    expect(hideLoading).toHaveBeenCalledOnce();
  });

  it("restores a non-zero short-material canvas and keeps generated templates synchronized", () => {
    const source = String(sharedMaterialSourceRaw);
    expect(source).toContain("const height = scrollHeight > 0 ? scrollHeight : viewportHeight");
    expect(source).toContain("if (height > 0");

    const canonical = String(sharedMaterialTemplateRaw);
    for (const replica of [
      String(practiceMaterialTemplateRaw),
      String(mockMaterialTemplateRaw),
      String(reflectionMaterialTemplateRaw),
    ]) {
      expect(replica).toBe(canonical);
      expect(replica).toContain('content-height="{{annotationContentHeight}}"');
      expect(replica).toContain('bindfocus="focusAnnotationCanvas"');
    }
  });
});
