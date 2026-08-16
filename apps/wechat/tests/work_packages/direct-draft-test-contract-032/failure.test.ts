import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLifecycleRaw from "../../xingce-practice-page-lifecycle.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLifecycleRaw from "../../stage2-mock-exam.test.ts?raw";

describe("direct draft test contract 032 failure path", () => {
  it("does not keep the obsolete single-surface gate as expected behavior", () => {
    for (const source of [String(practiceLifecycleRaw), String(mockLifecycleRaw)]) {
      expect(source).not.toContain(
        "mode=\\\"{{annotationMode === 'annotate' && annotationSurface === 'question' ? 'annotate' : 'static'}}\\\"",
      );
      expect(source).toContain('surfaceSwitchAvailable="{{false}}"');
    }
  });
});
