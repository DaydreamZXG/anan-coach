import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceSourceRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockSourceRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionSourceRaw from "../../../miniprogram/pages/reflection/index.ts?raw";

describe("workspace scratch and visible cause 035 failure guards", () => {
  it("removes split active canvases, material replication and automatic cause departure", () => {
    for (const source of [String(practiceSourceRaw), String(mockSourceRaw)]) {
      expect(source).not.toContain("changeMaterialAnnotations");
      expect(source).not.toContain("openMaterialAnnotation");
    }
    expect(String(practiceSourceRaw)).not.toContain("SHARED_MATERIAL_ANNOTATIONS_CHANGED");
    for (const template of [String(practiceTemplateRaw), String(mockTemplateRaw)]) {
      expect(template).not.toContain('bindstrokeschange="changeMaterialAnnotations"');
      expect(template).not.toContain("annotationCanvas === 'material'");
    }
    expect(String(reflectionSourceRaw)).not.toContain("岸岸会在后台整理");
  });
});
