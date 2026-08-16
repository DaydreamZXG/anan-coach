import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import preferenceControllerRaw from "../../../miniprogram/plans/shared/features/onboarding/study-preference-controller.ts?raw";
import type { AnnotationStroke } from "../../../miniprogram/task-runtime/contracts";
import { splitAnnotationSurfaces } from "../../../subpackage-shared-src/task-runtime/annotation-layer/strokes";
import { preferredNonEmptyMasteryBucket } from "../../../miniprogram/xingce/shared/features/xingce/mastery/presentation";

describe("full product audit repair 033a stale path", () => {
  it("reconciles a stale default mastery tab to the first useful queue", () => {
    expect(
      preferredNonEmptyMasteryBucket(
        {
          pendingReflectionCount: 0,
          dueVerificationCount: 3,
          stableMasteryCount: 4,
          invalidatedCount: 1,
        },
        "pending_reflection",
      ),
    ).toBe("due_verification");
  });

  it("lets settings load a default draft when legacy onboarding says preferences are not ready", () => {
    const source = String(preferenceControllerRaw);
    expect(source).toContain(
      'if (this.mode === "onboarding" && progress.nextStep !== "study_preference")',
    );
    expect(source).toContain("if (progress.studyPreferenceReady)");
    expect(source).not.toContain('this.mode === "settings" && !progress.studyPreferenceReady');
  });

  it("keeps old material strokes when their legacy ID is replayed", () => {
    const legacy = stroke("material-stroke-old");
    const question = stroke("question-stroke-old");
    expect(splitAnnotationSurfaces([legacy, question])).toEqual({
      material: [{ ...legacy, surface: "material" }],
      question: [question],
    });
  });
});

function stroke(strokeId: string): AnnotationStroke {
  return {
    strokeId,
    tool: "pen",
    width: 0.01,
    points: [
      { x: 0.1, y: 0.1 },
      { x: 0.2, y: 0.2 },
    ],
    atMs: 10,
  };
}
