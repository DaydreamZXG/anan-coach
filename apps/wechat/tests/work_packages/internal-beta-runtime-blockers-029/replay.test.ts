import { describe, expect, it } from "vitest";
// @ts-expect-error Vite exposes Mini Program source through its raw asset loader.
import essayLogicRaw from "../../../miniprogram/pages/essay-task/index.ts?raw";

describe("internal beta runtime blockers 029 replay path", () => {
  it("treats an existing answer version as completed truth before resuming work", () => {
    const source = String(essayLogicRaw);
    const truthGuard = source.indexOf(
      "if (draftingStage && workspace.progression.answerVersionId)",
    );
    const submitProjection = source.indexOf("canSubmit:", truthGuard);
    const resumeProjection = source.indexOf(
      'if (runtime.pendingDraftingSubmission?.phase === "completion_pending")',
      submitProjection,
    );

    expect(truthGuard).toBeGreaterThan(-1);
    expect(source.slice(truthGuard, submitProjection)).toContain(
      "runtime.pendingDraftingSubmission = null",
    );
    expect(source.slice(submitProjection, resumeProjection)).toContain(
      "Boolean(workspace.progression.answerVersionId)",
    );
  });
});
