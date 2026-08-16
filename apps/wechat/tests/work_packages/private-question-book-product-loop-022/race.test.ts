import { describe, expect, it, vi } from "vitest";
import type {
  PrivateQuestionBookDetail,
  PrivateQuestionBookPracticeLaunch,
} from "../../../miniprogram/api/generated/types.gen";
import { QuestionBookFlow } from "../../../miniprogram/pages/capture-import/question-book-flow";
import { bookDetail, confirmedWorkspace, planTaskId, practiceLaunch } from "./harness";

describe("private question book product loop 022 duplicate races", () => {
  it("coalesces parallel workspace notifications and repeated start taps", async () => {
    let resolveSave!: (book: PrivateQuestionBookDetail) => void;
    const saveResult = new Promise<PrivateQuestionBookDetail>((resolve) => {
      resolveSave = resolve;
    });
    let resolveStart!: (launch: PrivateQuestionBookPracticeLaunch) => void;
    const startResult = new Promise<PrivateQuestionBookPracticeLaunch>((resolve) => {
      resolveStart = resolve;
    });
    const createQuestionBook = vi.fn().mockReturnValue(saveResult);
    const startQuestionBookPractice = vi.fn().mockReturnValue(startResult);
    const flow = new QuestionBookFlow({
      gateway: {
        createQuestionBook,
        getQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        startQuestionBookPractice,
      },
      onChange: vi.fn(),
      createCommandKey: (prefix) => Promise.resolve(`${prefix}-race-key`),
    });

    const firstSave = flow.syncWorkspace(confirmedWorkspace(), "并发题本");
    const secondSave = flow.syncWorkspace(confirmedWorkspace(), "并发题本");
    resolveSave(bookDetail());
    await Promise.all([firstSave, secondSave]);

    const firstStart = flow.startPractice(planTaskId);
    const secondStart = flow.startPractice(planTaskId);
    resolveStart(practiceLaunch());
    await Promise.all([firstStart, secondStart]);

    expect(createQuestionBook).toHaveBeenCalledTimes(1);
    expect(startQuestionBookPractice).toHaveBeenCalledTimes(1);
  });
});
