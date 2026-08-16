import { describe, expect, it } from "vitest";

import { actualTarget, expectedTarget, neutralSource, xingceLearningTargets } from "./harness";

describe("subpackage teach-back sync happy path", () => {
  it("owns the complete teach-back surface in one neutral source and generates every target", async () => {
    const source = neutralSource();
    for (const phrase of [
      "teachBackAvailable: boolean",
      'contractVersion: "grounded-teach-back@v1"',
      'contractVersion: "teach-back-transfer-receipt@v1"',
      "startTeachBack(",
      "submitTeachBackTransfer(",
    ]) {
      expect(source).toContain(phrase);
    }
    for (const target of xingceLearningTargets) {
      expect(actualTarget(target)).toBe(await expectedTarget(target));
    }
  });
});
