import { describe, expect, it } from "vitest";
// @ts-expect-error Vite exposes Mini Program source through its raw asset loader.
import essayLogicRaw from "../../../miniprogram/pages/essay-task/index.ts?raw";

describe("internal beta runtime blockers 029 stale state", () => {
  it("clears stale local completion state when the workspace already owns an answer", () => {
    const source = String(essayLogicRaw);
    const start = source.indexOf("if (draftingStage && workspace.progression.answerVersionId)");
    const end = source.indexOf("const drafting =", start);
    const guard = source.slice(start, end);

    expect(guard).toContain("clearStoredDraftingSubmission");
    expect(guard).toContain("clearEssayDraftingDraft");
    expect(guard).toContain("pendingDrafting = null");
    expect(guard).toContain("pendingReview = null");
  });
});
