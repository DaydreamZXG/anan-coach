import { describe, expect, it } from "vitest";

// @ts-expect-error Vite exposes mini-program styles through its raw asset loader.
import practiceStyles from "../../../miniprogram/pages/practice/index.wxss?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import practiceTemplate from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program component templates through its raw asset loader.
import annotationTemplate from "../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/index.wxml?raw";

describe("RU-REAL-DEVICE-PRACTICE-WORKSPACE-035 layout race guard", () => {
  it("lets wrapped options grow and keeps the eliminate control inside the same card", () => {
    const styles = String(practiceStyles);
    expect(styles).not.toContain("grid-auto-rows: 1fr");
    expect(styles).toMatch(/\.option-card\s*\{[\s\S]*?align-items:\s*start;/);
    expect(styles).toMatch(/\.eliminate-action\s*\{[\s\S]*?position:\s*absolute;/);
  });

  it("contains one normal-workspace annotation layer and no dead surface switch UI", () => {
    expect(String(practiceTemplate).match(/strokePrefix="workspace-stroke"/g)).toHaveLength(1);
    expect(String(annotationTemplate)).not.toContain("草稿写在");
    expect(String(annotationTemplate)).not.toContain("switchSurface");
  });
});
