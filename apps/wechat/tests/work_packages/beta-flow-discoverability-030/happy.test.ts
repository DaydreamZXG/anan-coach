import { describe, expect, it } from "vitest";

// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import captureTemplateRaw from "../../../miniprogram/pages/capture-import/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import abilityLogicRaw from "../../../miniprogram/pages/ability/index.ts?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import practiceTemplateRaw from "../../../miniprogram/pages/practice/index.wxml?raw";
// @ts-expect-error Vite raw fixtures intentionally have no declarations.
import annotationTemplateRaw from "../../../subpackage-shared-src/task-runtime/annotation-layer/index.wxml?raw";

describe("beta flow discoverability 030 happy path", () => {
  it("offers one understandable path for PDF, fresh wrong questions, and cross-surface draft", () => {
    const capture = String(captureTemplateRaw);
    const materialActions = capture.indexOf('class="material-actions"');
    const pdfUpload = capture.indexOf('class="pdf-upload-card tap-tactile"');

    expect(materialActions).toBeGreaterThan(-1);
    expect(pdfUpload).toBeGreaterThan(materialActions);
    expect(capture.slice(materialActions, pdfUpload)).not.toContain("choosePdf");
    expect(capture).toContain("上传 PDF 题本");
    expect(capture).toContain('bindtap="choosePdf"');

    expect(String(abilityLogicRaw)).toContain(
      "`${routes.xingceMastery}?bucket=pending_reflection`",
    );
    expect(String(practiceTemplateRaw)).toContain('binddraft="openAnnotation"');
    expect(String(practiceTemplateRaw)).toContain(
      'bindannotationsurfacechange="switchAnnotationSurface"',
    );
    expect(String(annotationTemplateRaw)).toContain("草稿写在");
    expect(String(annotationTemplateRaw)).toContain(">本题</button>");
    expect(String(annotationTemplateRaw)).toContain(">共享材料</button>");
  });
});
