import { describe, expect, it } from "vitest";

import {
  actualTarget,
  expectedTarget,
  generator,
  manifestEntries,
  xingceLearningTargets,
} from "./harness";

describe("subpackage teach-back sync recovery", () => {
  it("converges every target and preserves a valid complete generation plan", async () => {
    const comparisons = await Promise.all(
      xingceLearningTargets.map(async (target) => ({
        actual: actualTarget(target),
        expected: await expectedTarget(target),
      })),
    );

    expect(comparisons.every(({ actual, expected }) => actual === expected)).toBe(true);
    expect(generator.subpackageSharedPlanViolations(manifestEntries)).toEqual([]);
  });
});
