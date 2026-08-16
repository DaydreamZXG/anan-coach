import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import neutralMaterialReaderRaw from "../../../subpackage-shared-src/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceMaterialReaderRaw from "../../../miniprogram/pages/practice/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockMaterialReaderRaw from "../../../miniprogram/pages/mock-exam/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionMaterialReaderRaw from "../../../miniprogram/pages/reflection/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import importPlatformRaw from "../../../miniprogram/pages/capture-import/shared/features/external-learning/platform.ts?raw";

describe("beta flow discoverability 030 recovery path", () => {
  it("keeps generated runtimes synchronized and preserves PDF validation", () => {
    const neutral = String(neutralMaterialReaderRaw);
    for (const replica of [
      String(practiceMaterialReaderRaw),
      String(mockMaterialReaderRaw),
      String(reflectionMaterialReaderRaw),
    ]) {
      expect(replica).toBe(neutral);
      expect(replica).toContain('bindsurfacechange="switchAnnotationSurface"');
    }

    const platform = String(importPlatformRaw);
    expect(platform).toContain('wx.chooseMessageFile({ count: 1, type: "all" })');
    expect(platform).toContain('throw new Error("请选择 PDF 文件。")');
  });
});
