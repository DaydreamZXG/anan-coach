import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";

describe("direct draft canvas 031 happy path", () => {
  it("opens one viewport drawing layer across material and question with one toolbar", () => {
    for (const source of [String(practiceTemplateRaw), String(mockTemplateRaw)]) {
      expect(source).toContain(
        "mode=\"{{annotationMode === 'annotate' ? 'annotate' : 'static'}}\"",
      );
      expect(source).toContain('coordinate-space="viewport"');
      expect(source).toContain('stroke-prefix="workspace-stroke"');
      expect(source.match(/<annotation-layer/gu)).toHaveLength(1);
      expect(source).toContain('activeTool="{{annotationTool}}"');
    }
  });
});
