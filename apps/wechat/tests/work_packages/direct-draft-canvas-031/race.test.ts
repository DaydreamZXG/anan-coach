import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import annotationLogicRaw from "../../../subpackage-shared-src/task-runtime/annotation-layer/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLogicRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";

describe("direct draft canvas 031 race path", () => {
  it("synchronizes the latest tool while the single canvas owns local actions", () => {
    const annotation = String(annotationLogicRaw);
    expect(annotation).toContain('this.triggerEvent("toolchange", { tool })');
    expect(annotation).toContain('this.triggerEvent("focus")');

    for (const source of [String(practiceLogicRaw), String(mockLogicRaw)]) {
      expect(source).toContain("changeAnnotationTool(event)");
      expect(source).toContain("changeAnnotations(");
      expect(source).toContain("replaceWorkspaceAnnotations(");
      expect(source).not.toContain("changeMaterialAnnotations(");
    }
  });
});
