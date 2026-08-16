import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";

describe("direct draft canvas 031 happy path", () => {
  it("opens question and material drawing together with one toolbar owner", () => {
    for (const source of [String(practiceTemplateRaw), String(mockTemplateRaw)]) {
      expect(source).toContain("annotationOpen=\"{{annotationMode === 'annotate'}}\"");
      expect(source).toContain(
        "mode=\"{{annotationMode === 'annotate' ? 'annotate' : 'static'}}\"",
      );
      expect(source).toContain(
        "annotationToolbarAvailable=\"{{annotationCanvas === 'material' || materialOpen}}\"",
      );
      expect(source).toContain(
        "toolbarAvailable=\"{{annotationCanvas === 'question' && !materialOpen}}\"",
      );
      expect(source).toContain('annotationTool="{{annotationTool}}"');
      expect(source).toContain('activeTool="{{annotationTool}}"');
    }
  });
});
