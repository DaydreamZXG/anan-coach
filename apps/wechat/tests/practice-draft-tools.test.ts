import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationStroke } from "../miniprogram/task-runtime/contracts";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import annotationTemplate from "../miniprogram/task-runtime/annotation-layer/index.wxml?raw";

interface AnnotationTestData {
  mode: string;
  strokes: AnnotationStroke[];
  elapsedSeconds: number;
  elapsedMs: number;
  replayMs: number;
  tool: string;
  tools: Array<{ value: string; glyph: string; label: string }>;
  lassoSelectedCount: number;
  drawnStrokeCount: number;
}

interface AnnotationTestHost {
  data: AnnotationTestData;
  setData(patch: Record<string, unknown>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
}

interface CapturedAnnotationComponent {
  data: AnnotationTestData;
  observers: {
    "mode, strokes, replayMs"(this: AnnotationObserverHost): void;
  };
  methods: {
    clearAll(this: AnnotationTestHost): Promise<void>;
    openMore(this: AnnotationTestHost): void;
  };
}

interface AnnotationObserverHost extends AnnotationTestHost {
  data: AnnotationTestData & {
    segments: unknown[];
    scrollTop: number;
    contentHeight: number;
  };
  createSelectorQuery(): {
    select(selector: string): {
      fields(options: Record<string, boolean>): void;
    };
    exec(
      callback: (
        results: Array<{
          node: { width: number; height: number; getContext(type: string): CanvasContext };
          width: number;
          height: number;
          left: number;
          top: number;
        }>,
      ) => void,
    ): void;
  };
}

interface CanvasContext {
  scale(x: number, y: number): void;
  clearRect(x: number, y: number, width: number, height: number): void;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("practice drawable draft tools", () => {
  it("keeps only the obvious drawing modes without covering the question", async () => {
    const definition = await captureAnnotationComponent();

    expect(definition.data.tools.map((item) => item.value)).toEqual([
      "pan",
      "pen",
      "highlighter",
      "eraser",
    ]);
    expect(definition.data.tools.map((item) => item.label)).toEqual([
      "滑动",
      "画笔",
      "荧光笔",
      "橡皮",
    ]);
    expect(annotationTemplate).toContain('catchtap="undo">撤销</button>');
    expect(annotationTemplate).toContain('catchtap="clearAll">清空</button>');
    expect(annotationTemplate).toContain('catchtap="finish">完成</button>');
    expect(annotationTemplate).toContain('catchtap="openMore">更多</button>');
    expect(annotationTemplate).not.toContain("annotation-layer__badge");
    expect(annotationTemplate).not.toContain("自动保存");
    expect(annotationTemplate).not.toContain("批注中");
  });

  it("confirms a destructive clear and preserves the erased-stroke evidence", async () => {
    const showModal = vi.fn(() => Promise.resolve({ confirm: true, cancel: false }));
    const definition = await captureAnnotationComponent(showModal);
    const triggerEvent = vi.fn();
    const host: AnnotationTestHost = {
      data: {
        ...cloneData(definition.data),
        elapsedSeconds: 9,
        elapsedMs: 9_500,
        strokes: [stroke("stroke-1"), stroke("stroke-2")],
      },
      setData(patch) {
        Object.assign(this.data, patch);
      },
      triggerEvent,
    };

    await definition.methods.clearAll.call(host);

    expect(showModal).toHaveBeenCalledWith(
      expect.objectContaining({ title: "清空草稿？", confirmText: "清空", cancelText: "保留" }),
    );
    expect(triggerEvent).toHaveBeenCalledOnce();
    const [eventName, detail] = triggerEvent.mock.calls[0] as [
      string,
      { action: string; strokes: AnnotationStroke[] },
    ];
    expect(eventName).toBe("strokeschange");
    expect(detail.action).toBe("erased");
    expect(detail.strokes.map((item) => item.erasedAtMs)).toEqual([9_500, 9_500]);
  });

  it("exposes non-writing settings through a secondary event", async () => {
    const definition = await captureAnnotationComponent();
    const triggerEvent = vi.fn();
    const host: AnnotationTestHost = {
      data: { ...cloneData(definition.data), strokes: [] },
      setData: vi.fn(),
      triggerEvent,
    };

    definition.methods.openMore.call(host);

    expect(triggerEvent).toHaveBeenCalledWith("more");
  });

  it("rebinds the canvas after hidden mode destroys the previous question node", async () => {
    const definition = await captureAnnotationComponent();
    const firstContext = canvasContext();
    const secondContext = canvasContext();
    const nodes = [canvasNode(firstContext), canvasNode(secondContext)];
    let queryCount = 0;
    const host: AnnotationObserverHost = {
      data: {
        ...cloneData(definition.data),
        mode: "annotate",
        strokes: [],
        segments: [],
        scrollTop: 0,
        contentHeight: 800,
      },
      setData(patch) {
        Object.assign(this.data, patch);
      },
      triggerEvent: vi.fn(),
      createSelectorQuery() {
        const node = nodes[queryCount];
        queryCount += 1;
        if (!node) throw new Error("unexpected extra canvas query");
        return {
          select(selector) {
            expect(selector).toBe("#annotation-canvas");
            return { fields: vi.fn() };
          },
          exec(callback) {
            callback([{ node, width: 320, height: 480, left: 0, top: 0 }]);
          },
        };
      },
    };

    definition.observers["mode, strokes, replayMs"].call(host);
    host.data.mode = "hidden";
    definition.observers["mode, strokes, replayMs"].call(host);
    host.data.mode = "annotate";
    definition.observers["mode, strokes, replayMs"].call(host);

    expect(queryCount).toBe(2);
    expect(firstContext.scale).toHaveBeenCalledOnce();
    expect(secondContext.scale).toHaveBeenCalledOnce();
    expect(secondContext.clearRect).toHaveBeenCalled();
  });
});

async function captureAnnotationComponent(
  showModal = vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
): Promise<CapturedAnnotationComponent> {
  let captured: CapturedAnnotationComponent | undefined;
  vi.stubGlobal("Component", (definition: unknown) => {
    captured = definition as CapturedAnnotationComponent;
  });
  vi.stubGlobal("wx", {
    showModal,
    showToast: vi.fn(() => Promise.resolve()),
    nextTick: (callback: () => void) => callback(),
    getWindowInfo: () => ({ pixelRatio: 2 }),
  });
  vi.stubGlobal("setTimeout", vi.fn());
  await import("../miniprogram/task-runtime/annotation-layer/index");
  if (!captured) throw new Error("annotation layer was not registered");
  return captured;
}

function canvasContext(): CanvasContext {
  return {
    scale: vi.fn(),
    clearRect: vi.fn(),
  };
}

function canvasNode(context: CanvasContext) {
  return {
    width: 0,
    height: 0,
    getContext: vi.fn(() => context),
  };
}

function cloneData(data: AnnotationTestData): AnnotationTestData {
  return JSON.parse(JSON.stringify(data)) as AnnotationTestData;
}

function stroke(strokeId: string): AnnotationStroke {
  return {
    strokeId,
    tool: "pen",
    width: 0.006,
    atMs: 1_000,
    points: [
      { x: 0.1, y: 0.1 },
      { x: 0.2, y: 0.2 },
    ],
  };
}
