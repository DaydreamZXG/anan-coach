import { describe, expect, it } from "vitest";

import { actualTarget, expectedTarget, xingceLearningTargets } from "./harness";

describe("subpackage teach-back sync concurrent projection", () => {
  it("keeps each package output isolated during parallel generation", async () => {
    const outputs = await Promise.all(xingceLearningTargets.map(expectedTarget));

    expect(outputs).toHaveLength(3);
    for (const [index, target] of xingceLearningTargets.entries()) {
      expect(outputs[index]).toBe(actualTarget(target));
    }
    expect(outputs.every((output) => output.includes("startTeachBack("))).toBe(true);
  });
});
