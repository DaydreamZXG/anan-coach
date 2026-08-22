import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceLifecycleRaw from "../../xingce-practice-page-lifecycle.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockLifecycleRaw from "../../stage2-mock-exam.test.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import materialContractRaw from "../../mock-material-annotation.test.ts?raw";

describe("direct draft test contract 032 happy path", () => {
  it("makes historical suites assert one viewport drawing layer and one toolbar", () => {
    for (const source of [String(practiceLifecycleRaw), String(mockLifecycleRaw)]) {
      expect(source).toContain("annotationMode === 'annotate' ? 'annotate' : 'static'");
      expect(source).toContain('coordinate-space="viewport"');
      expect(source).toContain('stroke-prefix="workspace-stroke"');
    }
    expect(String(materialContractRaw)).toContain("replaceWorkspaceAnnotations");
  });
});
