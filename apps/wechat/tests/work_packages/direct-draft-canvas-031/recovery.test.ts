import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import neutralAnnotationRaw from "../../../subpackage-shared-src/task-runtime/annotation-layer/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceAnnotationRaw from "../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockAnnotationRaw from "../../../miniprogram/pages/mock-exam/shared/task-runtime/annotation-layer/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionAnnotationRaw from "../../../miniprogram/pages/reflection/shared/task-runtime/annotation-layer/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import neutralMaterialRaw from "../../../subpackage-shared-src/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceMaterialRaw from "../../../miniprogram/pages/practice/shared/task-runtime/material-reader/index.wxml?raw";

describe("direct draft canvas 031 recovery path", () => {
  it("keeps generated canvases synchronized and immersive material toolbar-capable", () => {
    const annotation = String(neutralAnnotationRaw);
    for (const replica of [
      String(practiceAnnotationRaw),
      String(mockAnnotationRaw),
      String(reflectionAnnotationRaw),
    ]) {
      expect(replica).toBe(annotation);
      expect(replica).toContain("toolbarAvailable");
      expect(replica).toContain("mode === 'annotate' && toolbarAvailable");
    }

    expect(String(practiceMaterialRaw)).toBe(String(neutralMaterialRaw));
    expect(String(neutralMaterialRaw)).toContain(
      'toolbarAvailable="{{annotationToolbarAvailable}}"',
    );
    expect(String(neutralMaterialRaw)).toContain('bindfocus="focusAnnotationCanvas"');
  });
});
