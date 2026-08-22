import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import annotationLogicRaw from "../../../subpackage-shared-src/task-runtime/annotation-layer/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";

describe("beta flow discoverability 030 stale data path", () => {
  it("keeps old question and material strokes on their original surfaces", () => {
    const annotation = String(annotationLogicRaw);
    const practice = String(practiceLogicRaw);
    const template = String(practiceTemplateRaw);

    expect(annotation).not.toContain("switchSurface(event");
    expect(annotation).not.toContain('triggerEvent("surfacechange"');
    expect(practice).toContain("replaceQuestionAnnotations(");
    expect(practice).toContain("replaceMaterialAnnotations(current, event.detail.strokes)");
    expect(template).toContain('strokes="{{questionAnnotations}}"');
    expect(template).toContain(
      'annotationStrokes="{{materialOpen ? materialAnnotations : emptyAnnotationStrokes}}"',
    );
  });
});
