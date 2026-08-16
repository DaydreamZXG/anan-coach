import { describe, expect, it, vi } from "vitest";
import { ApiClientError } from "../../../miniprogram/api/client";
import { QuestionBookFlow } from "../../../miniprogram/pages/capture-import/question-book-flow";
import { bookDetail, confirmedWorkspace, practiceLaunch } from "./harness";

describe("private question book product loop 022 uncertain replay", () => {
  it("reuses the exact save command identity after an uncertain transport result", async () => {
    const createQuestionBook = vi
      .fn()
      .mockRejectedValueOnce(
        new ApiClientError("网络结果未知", 0, "request-unknown", undefined, "transport_unknown"),
      )
      .mockResolvedValueOnce(bookDetail());
    const createCommandKey = vi.fn().mockResolvedValue("question-book-save-stable-key");
    const flow = new QuestionBookFlow({
      gateway: {
        createQuestionBook,
        getQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        startQuestionBookPractice: vi.fn().mockResolvedValue(practiceLaunch()),
      },
      onChange: vi.fn(),
      createCommandKey,
    });

    await flow.syncWorkspace(confirmedWorkspace(), "同一本题.pdf");
    const recovered = await flow.retrySave(confirmedWorkspace(), "同一本题.pdf");

    expect(recovered?.bookId).toBe("book-022");
    expect(createCommandKey).toHaveBeenCalledTimes(1);
    expect(createQuestionBook).toHaveBeenCalledTimes(2);
    expect(createQuestionBook.mock.calls[0]?.[1]).toBe("question-book-save-stable-key");
    expect(createQuestionBook.mock.calls[1]?.[1]).toBe("question-book-save-stable-key");
  });
});
