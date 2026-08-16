import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import captureTemplateRaw from "../../../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import reflectionLogicRaw from "../../../miniprogram/pages/reflection/index.ts?raw";

describe("beta flow discoverability 030 failure path", () => {
  it("does not hide PDF in capture controls or fresh wrong questions behind long-term patterns", () => {
    const capture = String(captureTemplateRaw);
    const frameCopy = capture.slice(
      capture.indexOf('class="camera-frame__copy"'),
      capture.indexOf('class="camera-track"'),
    );
    const practice = String(practiceTemplateRaw);
    const materialHead = practice.slice(
      practice.indexOf('class="practice-material-head"'),
      practice.indexOf("<material-reader"),
    );

    expect(frameCopy).not.toContain("PDF");
    expect(materialHead).not.toContain('bindtap="openMaterialAnnotation"');
    expect(String(mockTemplateRaw)).toContain('coordinate-space="viewport"');
    expect(String(mockTemplateRaw)).not.toContain("openMaterialAnnotation");
    expect(String(reflectionLogicRaw)).toContain("能力 → 刚做的错题与待复盘");
  });
});
