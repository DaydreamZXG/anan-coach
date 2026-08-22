import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import practiceSource from "../../../miniprogram/pages/practice/index.ts?raw";

describe("RU-REAL-DEVICE-PRACTICE-WORKSPACE-035 stale-state guard", () => {
  it("does not inject legacy material strokes into the normal split workspace or the next question", () => {
    expect(String(practiceTemplate)).toContain(
      'annotationStrokes="{{materialOpen ? materialAnnotations : emptyAnnotationStrokes}}"',
    );
    expect(String(practiceTemplate)).toContain('strokePrefix="workspace-stroke"');
    expect(String(practiceSource)).not.toContain("SHARED_MATERIAL_ANNOTATIONS_CHANGED");
    expect(String(practiceSource)).not.toContain("sharedMaterialAnnotationStrokes");
  });
});
