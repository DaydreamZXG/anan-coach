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
  pageLifetimes: {
    resize(this: AnnotationObserverHost): void;
  };
  methods: {
    clearAll(this: AnnotationTestHost): Promise<void>;
    openMore(this: AnnotationTestHost): void;
    onTouchStart(this: AnnotationObserverHost, event: WechatMiniprogram.TouchEvent): void;
    onTouchMove(this: AnnotationObserverHost, event: WechatMiniprogram.TouchEvent): void;
    onTouchEnd(this: AnnotationObserverHost): void;
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
  save?(): void;
  restore?(): void;
  beginPath?(): void;
  moveTo?(x: number, y: number): void;
  lineTo?(x: number, y: number): void;
  stroke?(): void;
  setLineDash?(segments: number[]): void;
  lineCap?: string;
  lineJoin?: string;
  strokeStyle?: string;
  lineWidth?: number;
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

  it("remeasures and rebinds the canvas after portrait-landscape rotation", async () => {
    const definition = await captureAnnotationComponent();
    const portraitContext = canvasContext();
    const landscapeContext = canvasContext();
    const fixtures = [
      { node: canvasNode(portraitContext), width: 320, height: 640, left: 0, top: 96 },
      { node: canvasNode(landscapeContext), width: 720, height: 360, left: 360, top: 64 },
    ];
    let queryCount = 0;
    const host: AnnotationObserverHost = {
      data: {
        ...cloneData(definition.data),
        mode: "annotate",
        strokes: [],
        segments: [],
        scrollTop: 120,
        contentHeight: 1_200,
      },
      setData(patch) {
        Object.assign(this.data, patch);
      },
      triggerEvent: vi.fn(),
      createSelectorQuery() {
        const fixture = fixtures[queryCount];
        queryCount += 1;
        if (!fixture) throw new Error("unexpected extra canvas query");
        return {
          select(selector) {
            expect(selector).toBe("#annotation-canvas");
            return { fields: vi.fn() };
          },
          exec(callback) {
            callback([fixture]);
          },
        };
      },
    };

    definition.observers["mode, strokes, replayMs"].call(host);
    definition.pageLifetimes.resize.call(host);

    expect(queryCount).toBe(2);
    expect(portraitContext.scale).toHaveBeenCalledWith(2, 2);
    expect(landscapeContext.scale).toHaveBeenCalledWith(2, 2);
    expect(landscapeContext.clearRect).toHaveBeenCalledWith(0, 0, 720, 360);
  });

  it("uses the rotated capture layer origin instead of a stale canvas offset", async () => {
    const definition = await captureAnnotationComponent();
    const context = drawableCanvasContext();
    const triggerEvent = vi.fn();
    const host: AnnotationObserverHost = {
      data: {
        ...cloneData(definition.data),
        mode: "annotate",
        strokes: [],
        segments: [],
        scrollTop: 120,
        contentHeight: 1_200,
      },
      setData(patch) {
        Object.assign(this.data, patch);
      },
      triggerEvent,
      createSelectorQuery() {
        return {
          select: () => ({ fields: vi.fn() }),
          exec(callback) {
            // 故意保留一个过早测得的 left/top；真实落笔必须使用事件目标的新原点。
            callback([
              { node: canvasNode(context), width: 720, height: 360, left: 300, top: 48 },
            ]);
          },
        };
      },
    };
    definition.observers["mode, strokes, replayMs"].call(host);

    definition.methods.onTouchStart.call(host, drawingTouch(540, 184, 360, 64));
    definition.methods.onTouchMove.call(host, drawingTouch(612, 244, 360, 64));
    definition.methods.onTouchEnd.call(host);

    const detail = triggerEvent.mock.calls.at(-1)?.[1] as { strokes: AnnotationStroke[] };
    expect(detail.strokes[0]?.points[0]).toEqual(expect.objectContaining({ x: 0.25, y: 0.2 }));
    expect(detail.strokes[0]?.points[1]).toEqual(expect.objectContaining({ x: 0.35, y: 0.25 }));
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

function drawableCanvasContext(): CanvasContext {
  return {
    ...canvasContext(),
    save: vi.fn(),
    restore: vi.fn(),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn(),
    stroke: vi.fn(),
    setLineDash: vi.fn(),
  };
}

function drawingTouch(
  pageX: number,
  pageY: number,
  offsetLeft: number,
  offsetTop: number,
): WechatMiniprogram.TouchEvent {
  const touch = { pageX, pageY, clientX: pageX, clientY: pageY, identifier: 1 };
  return {
    type: "touchmove",
    timeStamp: 1,
    target: { id: "capture", dataset: {}, offsetLeft, offsetTop },
    currentTarget: { id: "capture", dataset: {}, offsetLeft, offsetTop },
    detail: {},
    touches: [touch],
    changedTouches: [touch],
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
