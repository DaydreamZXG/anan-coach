import { describe, expect, it } from "vitest";
import {
  buildArtifactCommand,
  initialEssayFormDrafts,
} from "../../../miniprogram/pages/essay-task/features/forms";
import { essayWorkspace } from "./fixture";

describe("essay task closure 034 failure path", () => {
  it("does not silently classify a new answer point as a problem", () => {
    const workspace = essayWorkspace();
    const drafts = initialEssayFormDrafts(workspace);
    const point = drafts.points[0];
    if (!point) throw new Error("fixture requires one point draft");
    point.statement = "旧物承载个人记忆。";
    point.sourceSegmentIds = ["segment-5"];

    expect(point.category).toBe("");
    expect(() => buildArtifactCommand("essay_point_extraction", workspace, drafts)).toThrow(
      "属于哪类信息",
    );
  });
});
