import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";

describe("direct draft canvas 031 happy path", () => {
  it("uses one current-question workspace canvas and reserves material canvas for fullscreen", () => {
    for (const source of [String(practiceTemplateRaw), String(mockTemplateRaw)]) {
      expect(source).toContain('strokePrefix="workspace-stroke"');
      expect(source).toContain(
        "mode=\"{{annotationMode === 'annotate' ? 'annotate' : 'static'}}\"",
      );
      expect(source).toContain(
        "annotationOpen=\"{{materialOpen && annotationMode === 'annotate'}}\"",
      );
      expect(source).toContain('toolbarAvailable="{{true}}"');
      expect(source).not.toContain("annotationCanvas");
      expect(source).toContain('annotationTool="{{annotationTool}}"');
      expect(source).toContain('activeTool="{{annotationTool}}"');
    }
  });
});
