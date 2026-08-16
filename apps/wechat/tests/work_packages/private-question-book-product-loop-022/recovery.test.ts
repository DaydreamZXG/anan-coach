import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { questionBookImportUrl } from "../../../miniprogram/app/navigation/routes";
import { QuestionBookFlow } from "../../../miniprogram/pages/capture-import/question-book-flow";
import { bookDetail, practiceLaunch } from "./harness";

describe("private question book product loop 022 recovery projection", () => {
  it("restores one canonical book identity from My while the existing Today coach remains owner", async () => {
    const flow = new QuestionBookFlow({
      gateway: {
        createQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        getQuestionBook: vi.fn().mockResolvedValue(bookDetail()),
        startQuestionBookPractice: vi.fn().mockResolvedValue(practiceLaunch()),
      },
      onChange: vi.fn(),
    });
    const restored = await flow.loadBook("book-022");
    const returnUrl = questionBookImportUrl({
      bookId: restored?.bookId,
      importId: restored?.importId,
    });
    const today = readFileSync("miniprogram/pages/today/index.ts", "utf8");
    const todayMarkup = readFileSync("miniprogram/pages/today/index.wxml", "utf8");
    const meMarkup = readFileSync("miniprogram/pages/me/index.wxml", "utf8");
    const libraryMarkup = readFileSync("miniprogram/pages/private-knowledge/index.wxml", "utf8");

    expect(returnUrl).toBe(
      "/pages/capture-import/index?mode=book&importId=import-1&bookId=book-022",
    );
    expect(todayMarkup).toContain("我想加练");
    expect(meMarkup).toContain("学习资料");
    expect(libraryMarkup).toContain("我的题本");
    expect(today).toContain("coachRuntimes");
    expect(today).toContain("presentTodayDashboard");
  });
});
