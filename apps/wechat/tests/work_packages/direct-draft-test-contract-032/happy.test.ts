import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLifecycleRaw from "../../xingce-practice-page-lifecycle.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLifecycleRaw from "../../stage2-mock-exam.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import materialContractRaw from "../../mock-material-annotation.test.ts?raw";

describe("direct draft test contract 032 happy path", () => {
  it("makes historical suites assert continuous drawing and one toolbar", () => {
    for (const source of [String(practiceLifecycleRaw), String(mockLifecycleRaw)]) {
      expect(source).toContain("annotationMode === 'annotate' ? 'annotate' : 'static'");
      expect(source).toContain("annotationOpen=\\\"{{annotationMode === 'annotate'}}\\\"");
      expect(source).toContain("annotationCanvas === 'question' && !materialOpen");
    }
    expect(String(materialContractRaw)).toContain(
      "annotationCanvas === 'material' || materialOpen",
    );
  });
});
