import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import annotationLogicRaw from "../../../subpackage-shared-src/task-runtime/annotation-layer/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";

describe("beta flow discoverability 030 stale data path", () => {
  it("keeps legacy strokes stored while rendering only the current question workspace", () => {
    const annotation = String(annotationLogicRaw);
    const practice = String(practiceLogicRaw);
    const template = String(practiceTemplateRaw);

    expect(annotation).toContain('coordinateSpace: { type: String, value: "content" }');
    expect(practice).toContain("replaceWorkspaceAnnotations(");
    expect(practice).toContain("workspaceAnnotationStrokes(");
    expect(practice).not.toContain('type: "SHARED_MATERIAL_ANNOTATIONS_CHANGED"');
    expect(template).toContain('strokes="{{questionAnnotations}}"');
    expect(template).not.toContain('annotationStrokes="{{materialAnnotations}}"');
  });
});
