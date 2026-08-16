import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLogicRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import replayRuntimeRaw from "../../../miniprogram/pages/reflection/features/replay-runtime.ts?raw";

describe("direct draft canvas 031 replay path", () => {
  it("keeps old question and material strokes readable while workspace strokes stay current", () => {
    const practice = String(practiceLogicRaw);
    const mock = String(mockLogicRaw);

    expect(practice).toContain("replaceWorkspaceAnnotations(");
    expect(practice).not.toContain('type: "SHARED_MATERIAL_ANNOTATIONS_CHANGED"');
    expect(mock).toContain("replaceWorkspaceAnnotations(");
    expect(mock).toContain("workspaceAnnotationStrokes(");
    expect(String(replayRuntimeRaw)).toContain("splitAnnotationSurfaces");
  });
});
