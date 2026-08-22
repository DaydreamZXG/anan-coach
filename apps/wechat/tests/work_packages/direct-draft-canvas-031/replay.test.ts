import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLogicRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLogicRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import replayRuntimeRaw from "../../../miniprogram/pages/reflection/features/replay-runtime.ts?raw";

describe("direct draft canvas 031 replay path", () => {
  it("keeps old question and material strokes on their canonical reducers", () => {
    const practice = String(practiceLogicRaw);
    const mock = String(mockLogicRaw);

    expect(practice).toContain("replaceQuestionAnnotations(");
    expect(practice).toContain("replaceMaterialAnnotations(current, event.detail.strokes)");
    expect(mock).toContain("replaceQuestionAnnotations(");
    expect(mock).toContain("replaceMaterialAnnotations(");
    expect(String(replayRuntimeRaw)).toContain("splitAnnotationSurfaces");
  });
});
