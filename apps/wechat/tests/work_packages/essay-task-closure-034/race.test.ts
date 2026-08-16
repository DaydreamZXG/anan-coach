import { describe, expect, it } from "vitest";
import { presentEssayArtifactCompletion } from "../../../miniprogram/pages/essay-task/features/completion";
import { createEssayTaskPageRuntime } from "../../../miniprogram/pages/essay-task/features/page-runtime";
import { currentPlan } from "./fixture";

describe("essay task closure 034 race path", () => {
  it("keeps one navigation owner while repeated completion presentation stays deterministic", () => {
    const runtime = createEssayTaskPageRuntime();
    expect(runtime.completionNavigationInFlight).toBe(false);
    runtime.completionNavigationInFlight = true;
    expect(runtime.completionNavigationInFlight).toBe(true);

    const first = presentEssayArtifactCompletion(
      "essay_material_reading",
      currentPlan(),
      "plan-reading",
      false,
    );
    expect(
      presentEssayArtifactCompletion(
        "essay_material_reading",
        currentPlan(),
        "plan-reading",
        false,
      ),
    ).toEqual(first);
  });
});
