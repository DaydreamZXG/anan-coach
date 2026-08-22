import { describe, expect, it } from "vitest";

import type { AnnotationStroke } from "../../../miniprogram/task-runtime/contracts";
import {
  replaceQuestionAnnotations,
  splitAnnotationSurfaces,
} from "../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/strokes";

function stroke(strokeId: string, surface?: "question" | "material"): AnnotationStroke {
  return {
    strokeId,
    ...(surface ? { surface } : {}),
    tool: "pen",
    width: 0.006,
    points: [
      { x: 0.1, y: 0.1 },
      { x: 0.2, y: 0.2 },
    ],
    atMs: 1,
  };
}

describe("RU-REAL-DEVICE-PRACTICE-WORKSPACE-035 replay isolation", () => {
  it("replaces only the current question workspace and preserves its immersive material marks", () => {
    const current = [stroke("workspace-old"), stroke("material-stroke-legacy", "material")];
    const next = replaceQuestionAnnotations(current, [stroke("workspace-current")]);

    expect(splitAnnotationSurfaces(next).question.map((item) => item.strokeId)).toEqual([
      "workspace-current",
    ]);
    expect(splitAnnotationSurfaces(next).material.map((item) => item.strokeId)).toEqual([
      "material-stroke-legacy",
    ]);
    expect(current.map((item) => item.strokeId)).toEqual([
      "workspace-old",
      "material-stroke-legacy",
    ]);
  });
});
