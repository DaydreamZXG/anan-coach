import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import interactionStateRaw from "../../../subpackage-shared-src/task-runtime/interaction/state.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLogicRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";

describe("beta flow discoverability 030 race path", () => {
  it("keeps one workspace owner while fullscreen legacy material remains recoverable", () => {
    const interaction = String(interactionStateRaw);
    expect(interaction).toContain(
      'return { ...state, overlay: "none", annotationSurface: action.surface };',
    );

    for (const source of [String(practiceLogicRaw), String(mockLogicRaw)]) {
      expect(source).not.toContain("switchAnnotationSurface(event)");
      expect(source).toContain('type: "ANNOTATION_OPENED", surface: "question"');
      expect(source).toContain('type: "ANNOTATION_OPENED", surface: "material"');
      expect(source).toContain("changeAnnotations(");
      expect(source).toContain("changeMaterialAnnotations(");
    }
  });
});
