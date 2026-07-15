import { describe, expect, it } from "vitest";

import {
  isTaskToolAllowed,
  MOCK_PRE_SUBMIT_ALLOWED_TOOLS,
  taskToolPolicy,
  type ObjectiveTaskExperience,
} from "../miniprogram/task-runtime/tool-policy";

describe("stage 2 objective task tool policy", () => {
  it("blocks answer-revealing tools before submission in every objective mode", () => {
    const modes: ObjectiveTaskExperience[] = ["targeted", "retest", "mock"];

    for (const mode of modes) {
      expect(taskToolPolicy(mode, "answering")).toMatchObject({
        analysis: false,
        formula_card: false,
        solution_ai: false,
      });
    }
  });

  it("limits a pre-submit mock to answer controls, display, disputes and operational help", () => {
    expect(MOCK_PRE_SUBMIT_ALLOWED_TOOLS).toEqual([
      "answer",
      "draft",
      "eliminate",
      "mark",
      "answer_sheet",
      "display_settings",
      "dispute_report",
      "operation_help",
      "fault_help",
    ]);
    expect(isTaskToolAllowed("mock", "answering", "analysis")).toBe(false);
    expect(isTaskToolAllowed("mock", "answering", "formula_card")).toBe(false);
    expect(isTaskToolAllowed("mock", "answering", "solution_ai")).toBe(false);
  });

  it("unlocks report-time learning tools only after submission", () => {
    expect(taskToolPolicy("mock", "submitted")).toMatchObject({
      answer: false,
      draft: false,
      eliminate: false,
      mark: false,
      analysis: true,
      formula_card: true,
      solution_ai: true,
    });
  });
});
