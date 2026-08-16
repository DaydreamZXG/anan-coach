import { describe, expect, it } from "vitest";

import {
  replaceWorkspaceAnnotations,
  splitAnnotationSurfaces,
  workspaceAnnotationStrokes,
} from "../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/strokes";

describe("workspace scratch and visible cause 035 replay", () => {
  it("keeps legacy strokes intact and replays only the owning question workspace", () => {
    const legacyQuestion = stroke("legacy-question");
    const legacyMaterial = { ...stroke("material-stroke-legacy"), surface: "material" as const };
    const questionOne = replaceWorkspaceAnnotations(
      [legacyQuestion, legacyMaterial],
      [stroke("workspace-stroke-q1")],
    );
    const questionTwo = replaceWorkspaceAnnotations([], [stroke("workspace-stroke-q2")]);

    expect(workspaceAnnotationStrokes(questionOne).map((item) => item.strokeId)).toEqual([
      "workspace-stroke-q1",
    ]);
    expect(workspaceAnnotationStrokes(questionTwo).map((item) => item.strokeId)).toEqual([
      "workspace-stroke-q2",
    ]);
    expect(splitAnnotationSurfaces(questionOne).material[0]?.strokeId).toBe(
      "material-stroke-legacy",
    );
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
