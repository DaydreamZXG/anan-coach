import { describe, expect, it } from "vitest";

import { expectedTarget, xingceLearningTargets } from "./harness";

describe("subpackage teach-back sync replay", () => {
  it("generates byte-identical output from the same source", async () => {
    for (const target of xingceLearningTargets) {
      expect(await expectedTarget(target)).toBe(await expectedTarget(target));
    }
  });
});
