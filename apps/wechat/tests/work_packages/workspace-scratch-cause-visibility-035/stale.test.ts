import { describe, expect, it } from "vitest";

import { replaceWorkspaceAnnotations } from "../../../miniprogram/pages/mock-exam/shared/task-runtime/annotation-layer/strokes";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceSourceRaw from "../../../miniprogram/pages/practice/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockSourceRaw from "../../../miniprogram/pages/mock-exam/index.ts?raw";

describe("workspace scratch and visible cause 035 stale state", () => {
  it("drops stale workspace strokes without deleting legacy evidence and closes on question change", () => {
    const next = replaceWorkspaceAnnotations(
      [stroke("legacy-question"), stroke("workspace-stroke-old")],
      [stroke("workspace-stroke-current")],
    );
    expect(next.map((item) => item.strokeId)).toEqual([
      "legacy-question",
      "workspace-stroke-current",
    ]);
    for (const source of [String(practiceSourceRaw), String(mockSourceRaw)]) {
      expect(source).toContain('{ type: "QUESTION_CHANGED" }');
    }
  });
});

function stroke(strokeId: string) {
  return {
    strokeId,
    tool: "pen" as const,
    width: 0.006,
    points: [
      { x: 0.1, y: 0.2 },
      { x: 0.3, y: 0.4 },
    ],
    atMs: 100,
  };
}
