import { describe, expect, it } from "vitest";

import {
  replaceWorkspaceAnnotations,
  workspaceAnnotationStrokes,
} from "../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/strokes";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionTemplateRaw from "../../../miniprogram/pages/reflection/index.wxml?raw";

describe("workspace scratch and visible cause 035 happy path", () => {
  it("uses one viewport canvas, owns strokes by question and leads to a visible coach candidate", () => {
    for (const source of [String(practiceTemplateRaw), String(mockTemplateRaw)]) {
      expect(source.match(/<annotation-layer/gu)).toHaveLength(1);
      expect(source).toContain('coordinate-space="viewport"');
      expect(source).toContain('stroke-prefix="workspace-stroke"');
      expect(source).toContain("整屏草稿");
    }

    const current = replaceWorkspaceAnnotations([], [stroke("workspace-stroke-q1")]);
    expect(workspaceAnnotationStrokes(current).map((item) => item.strokeId)).toEqual([
      "workspace-stroke-q1",
    ]);
    expect(String(reflectionTemplateRaw)).toContain("岸岸更怀疑");
    expect(String(reflectionTemplateRaw)).toContain("下一题怎么验证");
  });
});

function stroke(strokeId: string) {
  return {
    strokeId,
    tool: "pen" as const,
    width: 0.006,
    points: [
      { x: 0.1, y: 0.2 },
      { x: 0.8, y: 0.7 },
    ],
    atMs: 100,
  };
}
