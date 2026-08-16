import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLogicRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";

describe("direct draft test contract 032 race path", () => {
  it("keeps latest tool ownership separate from persistent stroke ownership", () => {
    for (const source of [String(practiceLogicRaw), String(mockLogicRaw)]) {
      expect(source).toContain("changeAnnotationTool(event)");
      expect(source).toContain("focusQuestionAnnotation()");
      expect(source).toContain("focusMaterialAnnotation()");
      expect(source).toContain("changeAnnotations(event)");
      expect(source).toContain("changeMaterialAnnotations(event)");
    }
  });
});
