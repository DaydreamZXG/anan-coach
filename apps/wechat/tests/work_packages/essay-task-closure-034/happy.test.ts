import { describe, expect, it } from "vitest";
import { presentEssayArtifactCompletion } from "../../../miniprogram/pages/essay-task/features/completion";
import { pointExtractionGuide } from "../../../miniprogram/pages/essay-task/features/forms";
import { currentPlan, essayWorkspace } from "./fixture";

describe("essay task closure 034 happy path", () => {
  it("carries the saved reading evidence into an understandable next task", () => {
    expect(pointExtractionGuide(essayWorkspace())).toEqual({
      hasPriorReading: true,
      sourceLabel: "材料 5",
      overallTheme: "旧物的情感与文化价值",
      summaryItems: [{ label: "材料 5 · 整则梳理", summary: "旧物承载记忆并保留传统手艺" }],
    });
    expect(
      presentEssayArtifactCompletion("essay_material_reading", currentPlan(), "plan-reading", true),
    ).toMatchObject({
      title: "这一步已经收好",
      nextAction: "从材料 5 提炼答案点",
    });
  });
});
