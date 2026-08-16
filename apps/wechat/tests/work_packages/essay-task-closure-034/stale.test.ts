import { describe, expect, it } from "vitest";
import {
  initialEssayFormDrafts,
  pointExtractionGuide,
} from "../../../miniprogram/pages/essay-task/features/forms";
import { essayWorkspace } from "./fixture";

describe("essay task closure 034 stale path", () => {
  it("does not fabricate a prior summary when the dependency is absent", () => {
    const workspace = essayWorkspace(false);

    expect(pointExtractionGuide(workspace)).toEqual({
      hasPriorReading: false,
      sourceLabel: "材料 5",
      overallTheme: "",
      summaryItems: [],
    });
    expect(initialEssayFormDrafts(workspace).points[0]).toMatchObject({
      category: "",
      categoryIndex: -1,
      sourceSegmentIds: [],
    });
  });
});
