import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import source from "../miniprogram/pages/mock-exam/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import readerTemplate from "../miniprogram/pages/mock-exam/shared/task-runtime/material-reader/index.wxml?raw";

describe("mock exam material scratch", () => {
  it("keeps question and material strokes separate while using one checkpoint", () => {
    expect(source).toContain("openMaterialAnnotation");
    expect(source).toContain("changeMaterialAnnotations");
    expect(source).toContain('surface: "material" as const');
    expect(template).toContain("annotationSurface === 'material'");
    expect(template).toContain('bindstrokeschange="changeMaterialAnnotations"');
    expect(readerTemplate).toContain('strokePrefix="material-stroke"');
  });
});
