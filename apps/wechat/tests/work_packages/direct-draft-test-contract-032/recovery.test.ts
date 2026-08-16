import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import packageRaw from "../../../package.json?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLifecycleRaw from "../../xingce-practice-page-lifecycle.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLifecycleRaw from "../../stage2-mock-exam.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import materialContractRaw from "../../mock-material-annotation.test.ts?raw";

describe("direct draft test contract 032 recovery path", () => {
  it("keeps all updated suites inside the normal full-package test command", () => {
    expect(String(packageRaw)).toContain('"test": "vitest run"');
    for (const source of [
      String(practiceLifecycleRaw),
      String(mockLifecycleRaw),
      String(materialContractRaw),
    ]) {
      expect(source).toContain("describe(");
      expect(source).toContain("it(");
    }
  });
});
