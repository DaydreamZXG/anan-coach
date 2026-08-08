import { describe, expect, it } from "vitest";
import {
  capturedHorizontalX,
  horizontalRegionAtPoint,
  renderedHorizontalX,
  type HorizontalAnnotationRegion,
} from "../miniprogram/pages/mock-exam/shared/task-runtime/annotation-layer/anchor";
import {
  replaceMaterialAnnotations,
  splitAnnotationSurfaces,
} from "../miniprogram/pages/mock-exam/shared/task-runtime/annotation-layer/strokes";

// @ts-expect-error Vite exposes mini-program sources through its raw asset loader.
import source from "../miniprogram/pages/mock-exam/index.ts?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import template from "../miniprogram/pages/mock-exam/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import readerTemplate from "../miniprogram/pages/mock-exam/shared/task-runtime/material-reader/index.wxml?raw";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import rendererTemplate from "../miniprogram/pages/mock-exam/shared/task-runtime/content-renderer/index.wxml?raw";

describe("mock exam material scratch", () => {
  it("keeps question and material strokes separate while using one checkpoint", () => {
    expect(source).toContain("openMaterialAnnotation");
    expect(source).toContain("changeMaterialAnnotations");
    expect(source).toContain("replaceMaterialAnnotations");
    expect(template).toContain("annotationSurface === 'material'");
    expect(template).toContain('bindstrokeschange="changeMaterialAnnotations"');
    expect(readerTemplate).toContain('strokePrefix="material-stroke"');
  });

  it("normalizes new and legacy material strokes without changing the checkpoint shape", () => {
    const question = {
      strokeId: "question-1",
      tool: "pen" as const,
      width: 0.006,
      points: [{ x: 0.1, y: 0.2 }],
      atMs: 100,
    };
    const legacyMaterial = {
      ...question,
      strokeId: "material-stroke-old",
    };
    const replacement = { ...question, strokeId: "material-stroke-new" };

    const next = replaceMaterialAnnotations([question, legacyMaterial], [replacement]);
    expect(next.map((stroke) => stroke.strokeId)).toEqual(["question-1", "material-stroke-new"]);
    expect(next[1]).toMatchObject({ surface: "material" });
    expect(splitAnnotationSurfaces([legacyMaterial]).material[0]).toMatchObject({
      strokeId: "material-stroke-old",
      surface: "material",
    });
    expect(next[1]).not.toHaveProperty("horizontalRegions");
  });

  it.each([
    ["phone", 390, 0],
    ["portrait tablet", 768, 160],
    ["landscape tablet", 1_024, 320],
  ])(
    "keeps table marks aligned through horizontal and vertical scroll on %s",
    (_, width, scrollTop) => {
      const region: HorizontalAnnotationRegion = {
        blockId: "table-data-analysis",
        label: "资料分析表",
        viewportLeft: 40,
        viewportTop: 260,
        viewportWidth: Math.min(600, width - 80),
        viewportHeight: 240,
        contentWidth: 800,
        scrollLeft: 180,
        measuredScrollTop: 0,
      };
      const metrics = {
        left: 0,
        top: 0,
        width,
        contentHeight: 1_600,
        scrollTop,
      };
      expect(horizontalRegionAtPoint([region], metrics, 220, 300 - scrollTop)?.blockId).toBe(
        "table-data-analysis",
      );
      const storedX = capturedHorizontalX(region, 220);
      expect(renderedHorizontalX(region, metrics, storedX)).toBeCloseTo(220);
      expect(renderedHorizontalX({ ...region, scrollLeft: 300 }, metrics, storedX)).toBeCloseTo(
        100,
      );
    },
  );

  it("wires table scroll measurements into both material-reader annotation surfaces", () => {
    expect(rendererTemplate).toContain('bindscroll="onTableScroll"');
    expect(rendererTemplate).toContain('data-block-id="{{item.blockId}}"');
    expect(readerTemplate).toMatch(
      /bindhorizontalregions="onHorizontalRegions"[\s\S]*bindhorizontalregions="onHorizontalRegions"/u,
    );
    expect(readerTemplate).toMatch(
      /horizontal-regions="\{\{horizontalRegions\}\}"[\s\S]*horizontal-regions="\{\{horizontalRegions\}\}"/u,
    );
  });
});
