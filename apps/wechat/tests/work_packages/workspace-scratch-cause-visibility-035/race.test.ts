import { describe, expect, it } from "vitest";

import { replaceWorkspaceAnnotations } from "../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/strokes";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionSourceRaw from "../../../miniprogram/pages/reflection/index.ts?raw";

describe("workspace scratch and visible cause 035 race handling", () => {
  it("converges rapid workspace replacements and keeps one reflection writer", () => {
    const first = replaceWorkspaceAnnotations([], [stroke("workspace-stroke-first")]);
    const latest = replaceWorkspaceAnnotations(first, [stroke("workspace-stroke-latest")]);
    expect(latest.map((item) => item.strokeId)).toEqual(["workspace-stroke-latest"]);

    const source = String(reflectionSourceRaw);
    expect(source).toContain("runtime.submitting || runtime.mediaBusy");
    expect(source).toContain('createIdempotencyKey("xingce-reflection")');
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
