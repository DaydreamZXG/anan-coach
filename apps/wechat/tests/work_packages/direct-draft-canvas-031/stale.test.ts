import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import annotationLogicRaw from "../../../subpackage-shared-src/task-runtime/annotation-layer/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";

describe("direct draft canvas 031 stale path", () => {
  it("resets stale tools to pan without mutating persisted strokes", () => {
    const annotation = String(annotationLogicRaw);
    const activeToolObserver = annotation.slice(
      annotation.indexOf("activeTool(this:"),
      annotation.indexOf("lifetimes:"),
    );

    expect(activeToolObserver).toContain("normalizeAnnotationTool");
    expect(activeToolObserver).toContain("state.activeStroke = null");
    expect(activeToolObserver).not.toContain("strokeschange");
    expect(String(practiceLogicRaw)).toContain('annotationTool: "pan"');
    expect(String(practiceLogicRaw)).toContain("workspaceAnnotationStrokes");
    expect(String(practiceLogicRaw)).not.toContain("annotationCanvas");
  });
});
