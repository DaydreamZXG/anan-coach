import { describe, expect, it, vi } from "vitest";
import { CaptureImportController } from "../../../miniprogram/pages/capture-import/features/external-learning/capture-controller";
import { contentMatch, gateway, questionUnit } from "../../external-learning/fixtures";
import { confirmedWorkspace } from "./harness";

describe("private question book product loop 022 partial matching", () => {
  it("interrupts only for ambiguity while unmatched questions cannot block matched practice", async () => {
    const ready = questionUnit({ state: "ready", questionUnitId: "ready-unit" });
    const unmatched = questionUnit({
      state: "unmatched",
      questionUnitId: "unmatched-unit",
      match: null,
    });
    const completeController = new CaptureImportController({
      gateway: gateway({
        getImport: vi
          .fn()
          .mockResolvedValue(
            confirmedWorkspace({ state: "review", questionUnits: [ready, unmatched] }),
          ),
      }),
      bookMode: true,
      onChange: vi.fn(),
    });
    await completeController.load("import-1");

    const ambiguous = questionUnit({
      state: "match_review",
      questionUnitId: "ambiguous-unit",
      match: contentMatch({
        questionUnitId: "ambiguous-unit",
        candidates: [
          {
            contentVersionId: "candidate-a",
            matchKind: "similar",
            score: 0.84,
            evidenceRefs: [],
          },
          {
            contentVersionId: "candidate-b",
            matchKind: "similar",
            score: 0.81,
            evidenceRefs: [],
          },
        ],
      }),
    });
    const reviewController = new CaptureImportController({
      gateway: gateway({
        getImport: vi
          .fn()
          .mockResolvedValue(
            confirmedWorkspace({ state: "review", questionUnits: [ready, unmatched, ambiguous] }),
          ),
      }),
      bookMode: true,
      onChange: vi.fn(),
    });
    await reviewController.load("import-1");

    expect(completeController.snapshot().status).toBe("success");
    expect(reviewController.snapshot().status).toBe("review");
    expect(reviewController.snapshot().activeQuestionUnit?.questionUnitId).toBe("ambiguous-unit");
  });
});
