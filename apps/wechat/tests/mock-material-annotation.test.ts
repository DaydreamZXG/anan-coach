import { afterEach, describe, expect, it, vi } from "vitest";
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

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

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

  it.each([
    ["phone", 390, 640, 120],
    ["portrait tablet", 768, 900, 240],
    ["landscape tablet", 1_024, 600, 320],
  ])(
    "measures real renderer and reader scroll contracts on %s",
    async (_, width, viewportHeight, scrollTop) => {
      const definitions: MiniComponentDefinition[] = [];
      vi.stubGlobal("Component", (definition: MiniComponentDefinition) => {
        definitions.push(definition);
      });
      await import("../miniprogram/pages/mock-exam/shared/task-runtime/content-renderer/index");
      await import("../miniprogram/pages/mock-exam/shared/task-runtime/material-reader/index");
      const [rendererDefinition, readerDefinition] = definitions;
      if (!rendererDefinition || !readerDefinition) {
        throw new Error("renderer and reader components must both register");
      }

      const viewportWidth = Math.min(600, width - 80);
      const rendererEvents: ComponentEvent[] = [];
      const renderer = componentHost(rendererDefinition, {
        createSelectorQuery: () => tableSelectorQuery(viewportWidth),
        triggerEvent: (name: string, detail?: Record<string, unknown>) => {
          rendererEvents.push({ name, detail });
        },
      });
      renderer.onTableScroll?.call(renderer);
      const horizontal = rendererEvents.find(({ name }) => name === "horizontalregions")?.detail as
        { regions: HorizontalAnnotationRegion[] } | undefined;
      expect(horizontal?.regions).toEqual([
        {
          blockId: "table-data-analysis",
          label: "资料分析表",
          viewportLeft: 40,
          viewportTop: 260,
          viewportWidth,
          viewportHeight: 240,
          contentWidth: 800,
          scrollLeft: 180,
        },
      ]);

      const readerEvents: ComponentEvent[] = [];
      const reader = componentHost(readerDefinition, {
        data: {
          ...readerDefinition.data,
          readerMode: "fullscreen",
          position: { anchorId: "material-start", scrollTop: 0, zoom: 1 },
          preferences: { fontScale: 1, colorMode: "standard", lineSpacing: 1 },
        },
        createSelectorQuery: () => readerSelectorQuery(viewportHeight),
        triggerEvent: (name: string, detail?: Record<string, unknown>) => {
          readerEvents.push({ name, detail });
        },
      });
      reader.onHorizontalRegions?.call(reader, {
        detail: horizontal,
      } as WechatMiniprogram.CustomEvent<{ regions: HorizontalAnnotationRegion[] }>);
      reader.onScroll?.call(reader, {
        detail: { scrollTop, scrollHeight: 1_600 },
      } as WechatMiniprogram.ScrollViewScroll);

      expect(reader.data.position).toMatchObject({ scrollTop });
      expect(reader.data.annotationContentHeight).toBe(1_600);
      expect(reader.data.readPercent).toBe(
        Math.round(((scrollTop + viewportHeight) / 1_600) * 100),
      );
      expect(readerEvents.map(({ name }) => name)).toEqual(
        expect.arrayContaining(["statechange", "positionchange"]),
      );
      const region = (reader.data.horizontalRegions as HorizontalAnnotationRegion[])[0];
      if (!region) throw new Error("reader horizontal region is required");
      expect(region.measuredScrollTop).toBe(0);
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
      expect(renderedHorizontalX({ ...region, scrollLeft: 300 }, metrics, storedX)).toBeCloseTo(
        100,
      );
    },
  );
});

interface MiniComponentDefinition {
  data?: Record<string, unknown>;
  methods?: Record<string, (...args: never[]) => unknown>;
}

interface ComponentEvent {
  name: string;
  detail: Record<string, unknown> | undefined;
}

type ComponentHost = Record<string, unknown> & {
  data: Record<string, unknown>;
  setData(patch: Record<string, unknown>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
  createSelectorQuery(): unknown;
  onTableScroll?: () => void;
  onHorizontalRegions?: (
    event: WechatMiniprogram.CustomEvent<{ regions: HorizontalAnnotationRegion[] }>,
  ) => void;
  onScroll?: (event: WechatMiniprogram.ScrollViewScroll) => void;
};

function componentHost(
  definition: MiniComponentDefinition,
  overrides: Partial<ComponentHost>,
): ComponentHost {
  const host = {
    data: { ...definition.data },
    setData(this: ComponentHost, patch: Record<string, unknown>) {
      Object.assign(this.data, patch);
    },
    triggerEvent: vi.fn(),
    createSelectorQuery: vi.fn(),
    ...overrides,
    ...definition.methods,
  } as ComponentHost;
  return host;
}

function tableSelectorQuery(viewportWidth: number) {
  const query = {
    selectAll: vi.fn(() => query),
    fields: vi.fn(() => query),
    exec: vi.fn(
      (
        callback: (
          results: Array<
            Array<{
              dataset?: Record<string, unknown>;
              left?: number;
              top?: number;
              width?: number;
              height?: number;
              scrollLeft?: number;
            }>
          >,
        ) => void,
      ) => {
        callback([
          [
            {
              dataset: { blockId: "table-data-analysis", label: "资料分析表" },
              left: 40,
              top: 260,
              width: viewportWidth,
              height: 240,
              scrollLeft: 180,
            },
          ],
          [{ width: 800 }],
        ]);
      },
    ),
  };
  return query;
}

function readerSelectorQuery(viewportHeight: number) {
  const query = {
    select: vi.fn(() => query),
    boundingClientRect: vi.fn(() => query),
    exec: vi.fn((callback: (results: Array<{ height: number }>) => void) => {
      callback([{ height: viewportHeight }]);
    }),
  };
  return query;
}
