import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import materialContractRaw from "../../mock-material-annotation.test.ts?raw";

describe("direct draft test contract 032 replay path", () => {
  it("retains legacy and current material stroke identity coverage", () => {
    const source = String(materialContractRaw);
    expect(source).toContain("replaceMaterialAnnotations");
    expect(source).toContain("splitAnnotationSurfaces");
    expect(source).toContain('strokeId: "material-stroke-old"');
    expect(source).toContain('surface: "material"');
  });
});
