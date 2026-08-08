import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationStroke } from "../miniprogram/task-runtime/contracts";
import {
  checkpointCommand,
  runtimeFromXingceSession,
  runtimeWithPendingCheckpoint,
} from "../miniprogram/pages/practice/features/xingce/real_attempt/controller";
import {
  loadLocalXingceCheckpointDraft,
  storeLocalXingceCheckpointDraft,
} from "../miniprogram/pages/practice/shared/features/xingce/real_attempt/pending-store";
import { reduceTaskRuntime } from "../miniprogram/pages/practice/shared/task-runtime/state/runtime";
import {
  activeXingceSession,
  createMemoryWechatStorage,
  xingceIds,
} from "./work_packages/vj-02-real-xingce-wechat/harness";
// @ts-expect-error Vite exposes mini-program templates through its raw asset loader.
import annotationTemplate from "../miniprogram/pages/practice/shared/task-runtime/annotation-layer/index.wxml?raw";

interface AnnotationTestData {
  mode: string;
  strokes: AnnotationStroke[];
  elapsedSeconds: number;
  elapsedMs: number;
  replayMs: number;
  tool: string;
  toolbarCollapsed: boolean;
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
    scrollTop(this: AnnotationObserverHost): void;
  };
  pageLifetimes: {
    resize(this: AnnotationObserverHost): void;
  };
  methods: {
    collapseToolbar(this: AnnotationTestHost): void;
    expandToolbar(this: AnnotationTestHost): void;
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

describe("practice draft save and retry", () => {
  it("restores text and point evidence from the exact local checkpoint before retry", () => {
    const { wxStorage } = createMemoryWechatStorage();
    vi.stubGlobal("wx", wxStorage);
    const session = activeXingceSession({ revision: 7 });
    let runtime = runtimeFromXingceSession(session);
    runtime = reduceTaskRuntime(runtime, { type: "DRAFT_CHANGED", text: "增长率 = 增量 ÷ 基期" });
    runtime = reduceTaskRuntime(runtime, {
      type: "ANNOTATIONS_CHANGED",
      action: "added",
      strokes: [
        {
          strokeId: "material-stroke-retry",
          surface: "material",
          tool: "pen",
          width: 0.006,
          points: [
            { x: 0.4, y: 0.25, t: 0 },
            { x: 0.4001, y: 0.25, t: 1 },
          ],
          atMs: 600,
          anchor: { blockId: "table-1", segmentIndex: 0, segmentText: "资料表格" },
        },
      ],
    });
    const command = checkpointCommand(runtime);
    const draft = {
      kind: "checkpoint_draft" as const,
      planTaskId: xingceIds.planTask,
      sessionId: xingceIds.session,
      command,
    };

    expect(storeLocalXingceCheckpointDraft("user-a", draft)).toBe(true);
    const restored = loadLocalXingceCheckpointDraft("user-a", xingceIds.session);
    expect(restored?.command.answers[0]).toMatchObject({
      draftText: "增长率 = 增量 ÷ 基期",
      annotations: [
        expect.objectContaining({
          strokeId: "material-stroke-retry",
          surface: "material",
          anchor: { blockId: "table-1", segmentIndex: 0, segmentText: "资料表格" },
        }),
      ],
    });

    const retry = runtimeWithPendingCheckpoint(session, restored?.command ?? command);
    expect(checkpointCommand(retry).answers[0]).toEqual(command.answers[0]);
    expect(command.answers[0]?.actionEvents?.map((event) => event.type)).toEqual(
      expect.arrayContaining(["draft_edited", "annotation_added"]),
    );
  });
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
    expect(definition.data.tool).toBe("pan");
    expect(annotationTemplate).toContain("wx:if=\"{{mode === 'annotate' && tool !== 'pan'}}\"");
    expect(annotationTemplate).toContain('catchtap="undo">撤销</button>');
    expect(annotationTemplate).toContain('catchtap="clearAll">清空</button>');
    expect(annotationTemplate).toContain('catchtap="finish">完成</button>');
    expect(annotationTemplate).toContain('catchtap="openMore">更多</button>');
    expect(annotationTemplate).toContain('catchtap="collapseToolbar">收起</button>');
    expect(annotationTemplate).toContain('catchtap="expandToolbar"');
    expect(annotationTemplate).toContain('aria-label="草稿工具已收起"');
    expect(annotationTemplate).not.toContain("annotation-layer__badge");
    expect(annotationTemplate).not.toContain("自动保存");
    expect(annotationTemplate).not.toContain("批注中");
  });

  it("collapses into reading mode without leaving an invisible drawing shield", async () => {
    const definition = await captureAnnotationComponent();
    const host: AnnotationTestHost = {
      data: { ...cloneData(definition.data), strokes: [], tool: "highlighter" },
      setData(patch) {
        Object.assign(this.data, patch);
      },
      triggerEvent: vi.fn(),
    };

    definition.methods.collapseToolbar.call(host);

    expect(host.data.toolbarCollapsed).toBe(true);
    expect(host.data.tool).toBe("pan");
    expect(host.data.lassoSelectedCount).toBe(0);

    definition.methods.expandToolbar.call(host);
    expect(host.data.toolbarCollapsed).toBe(false);
    expect(host.data.tool).toBe("pan");
  });

  it("reopens every draft in scroll mode instead of restoring a full-screen drawing shield", async () => {
    const definition = await captureAnnotationComponent();
    const host = annotationObserverHost(definition, {
      mode: "annotate",
      tool: "highlighter",
      toolbarCollapsed: true,
      lassoSelectedCount: 2,
    });

    definition.observers["mode, strokes, replayMs"].call(host);

    expect(host.data.tool).toBe("pan");
    expect(host.data.toolbarCollapsed).toBe(false);
    expect(host.data.lassoSelectedCount).toBe(0);
  });

  it("coalesces repeated scroll redraws into one next frame", async () => {
    const nextTicks: Array<() => void> = [];
    const definition = await captureAnnotationComponent(undefined, (callback) => {
      nextTicks.push(callback);
    });
    const context = canvasContext();
    const host = annotationObserverHost(definition, { mode: "annotate" }, context);

    definition.observers["mode, strokes, replayMs"].call(host);
    expect(nextTicks).toHaveLength(1);
    nextTicks.shift()?.();

    host.data.scrollTop = 1;
    definition.observers.scrollTop.call(host);
    host.data.scrollTop = 2;
    definition.observers.scrollTop.call(host);
    host.data.scrollTop = 3;
    definition.observers.scrollTop.call(host);

    expect(nextTicks).toHaveLength(1);
    nextTicks.shift()?.();
    // eslint-disable-next-line @typescript-eslint/unbound-method
    expect(context.clearRect).toHaveBeenCalledTimes(2);
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
    // eslint-disable-next-line @typescript-eslint/unbound-method
    expect(firstContext.scale).toHaveBeenCalledOnce();
    // eslint-disable-next-line @typescript-eslint/unbound-method
    expect(secondContext.scale).toHaveBeenCalledOnce();
    // eslint-disable-next-line @typescript-eslint/unbound-method
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
    // eslint-disable-next-line @typescript-eslint/unbound-method
    expect(portraitContext.scale).toHaveBeenCalledWith(2, 2);
    // eslint-disable-next-line @typescript-eslint/unbound-method
    expect(landscapeContext.scale).toHaveBeenCalledWith(2, 2);
    // eslint-disable-next-line @typescript-eslint/unbound-method
    expect(landscapeContext.clearRect).toHaveBeenCalledWith(0, 0, 720, 360);
  });

  it("keeps portrait strokes aligned to the measured canvas viewport rect", async () => {
    const definition = await captureAnnotationComponent();
    const context = drawableCanvasContext();
    const triggerEvent = vi.fn();
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
      triggerEvent,
      createSelectorQuery() {
        return {
          select: () => ({ fields: vi.fn() }),
          exec(callback) {
            callback([{ node: canvasNode(context), width: 320, height: 600, left: 40, top: 80 }]);
          },
        };
      },
    };
    definition.observers["mode, strokes, replayMs"].call(host);
    host.data.tool = "pen";

    definition.methods.onTouchStart.call(host, clientTouch(120, 160));
    definition.methods.onTouchMove.call(host, clientTouch(152, 240));
    definition.methods.onTouchEnd.call(host);

    const detail = triggerEvent.mock.calls.at(-1)?.[1] as { strokes: AnnotationStroke[] };
    expect(detail.strokes[0]?.points[0]).toEqual(expect.objectContaining({ x: 0.25, y: 0.1 }));
    expect(detail.strokes[0]?.points[1]).toEqual(expect.objectContaining({ x: 0.35, y: 0.2 }));
  });

  it("keeps a tap or very short handwriting stroke instead of swallowing it", async () => {
    const definition = await captureAnnotationComponent();
    const triggerEvent = vi.fn();
    const host = annotationObserverHost(definition, { mode: "annotate" }, drawableCanvasContext());
    host.triggerEvent = triggerEvent;
    definition.observers["mode, strokes, replayMs"].call(host);
    host.data.tool = "pen";

    definition.methods.onTouchStart.call(host, clientTouch(80, 140));
    definition.methods.onTouchEnd.call(host);

    const detail = triggerEvent.mock.calls.at(-1)?.[1] as { strokes: AnnotationStroke[] };
    expect(detail.strokes).toHaveLength(1);
    expect(detail.strokes[0]?.points).toHaveLength(2);
    expect(detail.strokes[0]?.points[1]?.t).toBeGreaterThanOrEqual(1);
  });
});

async function captureAnnotationComponent(
  showModal: () => Promise<{ confirm: boolean; cancel: boolean }> = vi.fn(() =>
    Promise.resolve({ confirm: true, cancel: false }),
  ),
  nextTick: (callback: () => void) => void = (callback) => {
    callback();
  },
): Promise<CapturedAnnotationComponent> {
  let captured: CapturedAnnotationComponent | undefined;
  vi.stubGlobal("Component", (definition: unknown) => {
    captured = definition as CapturedAnnotationComponent;
  });
  vi.stubGlobal("wx", {
    showModal,
    showToast: vi.fn(() => Promise.resolve()),
    nextTick,
    getWindowInfo: () => ({ pixelRatio: 2 }),
  });
  vi.stubGlobal("setTimeout", vi.fn());
  await import("../miniprogram/pages/practice/shared/task-runtime/annotation-layer/index");
  if (!captured) throw new Error("annotation layer was not registered");
  return captured;
}

function annotationObserverHost(
  definition: CapturedAnnotationComponent,
  patch: Partial<AnnotationObserverHost["data"]> = {},
  context = canvasContext(),
): AnnotationObserverHost {
  return {
    data: {
      ...cloneData(definition.data),
      mode: "annotate",
      strokes: [],
      segments: [],
      scrollTop: 0,
      contentHeight: 800,
      ...patch,
    },
    setData(dataPatch) {
      Object.assign(this.data, dataPatch);
    },
    triggerEvent: vi.fn(),
    createSelectorQuery() {
      return {
        select: () => ({ fields: vi.fn() }),
        exec(callback) {
          callback([{ node: canvasNode(context), width: 320, height: 480, left: 0, top: 0 }]);
        },
      };
    },
  };
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

function clientTouch(clientX: number, clientY: number): WechatMiniprogram.TouchEvent {
  const touch = { clientX, clientY, pageX: clientX, pageY: clientY, identifier: 1 };
  return {
    type: "touchmove",
    timeStamp: 1,
    // 故意给出错误的组件局部 offset；实现必须使用 canvas 的视口 rect。
    target: { id: "capture", dataset: {}, offsetLeft: 0, offsetTop: 0 },
    currentTarget: { id: "capture", dataset: {}, offsetLeft: 0, offsetTop: 0 },
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
