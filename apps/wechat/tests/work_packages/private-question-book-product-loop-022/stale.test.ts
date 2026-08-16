import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import {
  preferredQuestionBookPlanTaskId,
  QuestionBookFlow,
} from "../../../miniprogram/pages/capture-import/question-book-flow";
import {
  bookDetail,
  confirmedWorkspace,
  practiceLaunch,
  todayDashboard,
  todayTask,
} from "./harness";

describe("private question book product loop 022 stale Today plan", () => {
  it("keeps the saved book and exposes adjustment instead of inventing a practice task", async () => {
    const startQuestionBookPractice = vi.fn().mockResolvedValue(practiceLaunch());
    const flow = new QuestionBookFlow({
      gateway: {
        createQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        getQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        startQuestionBookPractice,
      },
      onChange: vi.fn(),
      createCommandKey: () => Promise.resolve("stale-command-022"),
    });
    await flow.syncWorkspace(confirmedWorkspace(), "题本");
    const selected = preferredQuestionBookPlanTaskId(
      todayDashboard([todayTask({ status: "complete" })]),
    );
    const capturePage = readFileSync("miniprogram/pages/capture-import/index.wxml", "utf8");

    expect(selected).toBeNull();
    expect(flow.snapshot().book?.bookId).toBe("book-022");
    expect(startQuestionBookPractice).not.toHaveBeenCalled();
    expect(capturePage).toContain('bindtap="openQuestionBookAdjustment"');
    expect(capturePage).toContain("调整今天再练");
  });
});
