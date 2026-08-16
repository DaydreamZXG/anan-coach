import { describe, expect, it, vi } from "vitest";
import {
  preferredQuestionBookPlanTaskId,
  QuestionBookFlow,
} from "../../../miniprogram/pages/capture-import/question-book-flow";
import {
  bookDetail,
  confirmedWorkspace,
  planTaskId,
  practiceLaunch,
  todayDashboard,
  todayTask,
} from "./harness";

describe("private question book product loop 022 happy path", () => {
  it("saves the confirmed book and launches only the current registered Today practice", async () => {
    const createQuestionBook = vi.fn().mockResolvedValue(bookDetail());
    const startQuestionBookPractice = vi.fn().mockResolvedValue(practiceLaunch());
    const flow = new QuestionBookFlow({
      gateway: {
        createQuestionBook,
        getQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        startQuestionBookPractice,
      },
      onChange: vi.fn(),
      createCommandKey: (prefix) => Promise.resolve(`${prefix}-command-022`),
    });

    const saved = await flow.syncWorkspace(confirmedWorkspace(), "名师冲刺题本.pdf");
    const selectedTaskId = preferredQuestionBookPlanTaskId(
      todayDashboard([
        todayTask({ id: "invalid-first", startUrl: "/pages/me/index" }),
        todayTask(),
      ]),
    );
    const target = await flow.startPractice(selectedTaskId ?? "");

    expect(saved?.bookId).toBe("book-022");
    expect(createQuestionBook).toHaveBeenCalledWith(
      {
        importId: "import-1",
        recognitionRevisionId: "recognition-1",
        title: "名师冲刺题本",
      },
      "question-book-save-command-022",
    );
    expect(selectedTaskId).toBe(planTaskId);
    expect(startQuestionBookPractice).toHaveBeenCalledWith(
      "book-022",
      { planTaskId },
      "question-book-practice-command-022",
    );
    expect(target).toEqual({ mode: "navigateTo", url: practiceLaunch().startUrl });
  });
});
