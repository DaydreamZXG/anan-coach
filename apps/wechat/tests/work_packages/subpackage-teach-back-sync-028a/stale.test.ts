import { describe, expect, it } from "vitest";

import { expectedTarget, xingceLearningTargets } from "./harness";

describe("subpackage teach-back sync stale path rewriting", () => {
  it("never leaks neutral-source or project-root imports into generated packages", async () => {
    for (const target of xingceLearningTargets) {
      const expected = await expectedTarget(target);
      expect(expected).not.toContain("subpackage-shared-src");
      expect(expected).not.toContain('from "../../miniprogram/');
      expect(expected).toContain("api/generated/types.gen");
    }
  });
});
