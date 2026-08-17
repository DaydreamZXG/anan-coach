import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import mockAnswerCard from "../../../miniprogram/pages/mock-exam/shared/task-runtime/answer-sheet/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceAnswerCard from "../../../miniprogram/pages/practice/shared/task-runtime/answer-sheet/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import sharedAnswerCard from "../../../subpackage-shared-src/task-runtime/answer-sheet/index.wxml?raw";

function asText(value: unknown): string {
  if (typeof value !== "string") throw new Error("Raw mini-program asset must be text.");
  return value;
}

const answerCards = [
  asText(sharedAnswerCard),
  asText(practiceAnswerCard),
  asText(mockAnswerCard),
] as const;

describe("real-device answer-card recovery", () => {
  it("keeps one submit path and reports the real current question", () => {
    for (const template of answerCards) {
      expect(template).toContain("当前 {{current}}");
      expect(template).not.toContain("当前 1");
      expect(template).not.toContain("继续第 {{current}} 题");
      expect(template.match(/bindtap="submit"/gu)).toHaveLength(1);
      expect(template.match(/bindtap="close"/gu)).toHaveLength(1);
      expect(template.match(/catchtap="close"/gu)).toHaveLength(1);
    }
  });
});
