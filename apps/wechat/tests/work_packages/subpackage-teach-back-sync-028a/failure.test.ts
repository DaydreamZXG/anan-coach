import { describe, expect, it } from "vitest";

import { expectedTarget, generator, xingceLearningTargets } from "./harness";

describe("subpackage teach-back sync failure isolation", () => {
  it("detects one edited target without treating it as a new source", async () => {
    const target = xingceLearningTargets[0];
    if (!target) throw new Error("Expected a generated xingce-learning target.");
    const expected = await expectedTarget(target);

    expect(generator.generatedTargetIsStale(expected, expected)).toBe(false);
    expect(generator.generatedTargetIsStale(expected, `${expected}\n// simulated drift`)).toBe(
      true,
    );
  });
});
