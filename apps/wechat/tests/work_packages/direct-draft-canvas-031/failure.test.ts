import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import captureTemplateRaw from "../../../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import mockTemplateRaw from "../../../miniprogram/pages/mock-exam/index.wxml?raw";

describe("direct draft canvas 031 failure path", () => {
  it("does not expose a technical surface choice and keeps PDF recovery guidance", () => {
    for (const source of [String(practiceTemplateRaw), String(mockTemplateRaw)]) {
      expect(source).not.toContain("surfaceSwitchAvailable");
      expect(source).not.toContain("annotationSurfaceSwitchAvailable");
      expect(source).not.toContain("bindannotationsurfacechange");
      expect(source).toContain('strokePrefix="workspace-stroke"');
    }

    const practice = String(practiceTemplateRaw);
    expect(practice.match(/binddraft="openAnnotation"/g)).toHaveLength(1);
    expect(String(captureTemplateRaw)).toContain("上传 PDF 题本");
    expect(String(captureTemplateRaw)).toContain("从微信选 PDF");
    expect(String(captureTemplateRaw)).toContain("PDF 先发到文件传输助手或聊天");
  });
});
