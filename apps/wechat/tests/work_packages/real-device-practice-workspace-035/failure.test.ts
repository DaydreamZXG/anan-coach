import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationStroke } from "../../../miniprogram/task-runtime/contracts";

interface CapturedComponent {
  data: Record<string, unknown>;
  observers: Record<string, (this: AnnotationHost) => void>;
  methods: Record<string, (this: AnnotationHost, event?: WechatMiniprogram.TouchEvent) => void>;
}

interface AnnotationHost {
  data: Record<string, unknown> & { tool: string };
  setData(patch: Record<string, unknown>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("RU-REAL-DEVICE-PRACTICE-WORKSPACE-035 gesture failure guard", () => {
  it("keeps one physical stroke when a tablet gesture crosses the material-question divider", async () => {
    let definition: CapturedComponent | undefined;
    vi.stubGlobal("Component", (value: CapturedComponent) => {
      definition = value;
    });
    vi.stubGlobal("wx", {
      nextTick: (callback: () => void) => {
        callback();
      },
      getWindowInfo: () => ({ pixelRatio: 2 }),
      showToast: vi.fn(() => Promise.resolve()),
      showModal: vi.fn(() => Promise.resolve({ confirm: true, cancel: false })),
    });
    await import("../../../miniprogram/pages/practice/shared/task-runtime/annotation-layer/index");
    if (!definition) throw new Error("annotation component was not captured");

    const triggerEvent = vi.fn();
    const context = {
      scale: vi.fn(),
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke: vi.fn(),
      setLineDash: vi.fn(),
    };
    const canvas = { width: 0, height: 0, getContext: vi.fn(() => context) };
    const host: AnnotationHost = {
      data: {
        ...(JSON.parse(JSON.stringify(definition.data)) as Record<string, unknown>),
        mode: "annotate",
        strokes: [],
        segments: [],
        horizontalRegions: [],
        scrollTop: 0,
        contentHeight: 600,
        tool: "pan",
      },
      setData(patch) {
        Object.assign(this.data, patch);
      },
      triggerEvent,
      createSelectorQuery() {
        return {
          select: () => ({ fields: vi.fn() }),
          exec: (callback: (result: unknown[]) => void) => {
            callback([{ node: canvas, width: 1_000, height: 600, left: 0, top: 0 }]);
          },
        } as unknown as WechatMiniprogram.SelectorQuery;
      },
    };

    definition.observers["mode, strokes, replayMs"]?.call(host);
    host.data.tool = "pen";
    definition.methods.onTouchStart?.call(host, touchAt(300, 240));
    definition.methods.onTouchMove?.call(host, touchAt(700, 240));
    definition.methods.onTouchEnd?.call(host);

    const change = triggerEvent.mock.calls.find(([name]) => name === "strokeschange");
    const strokes = (change?.[1] as { strokes?: AnnotationStroke[] } | undefined)?.strokes ?? [];
    expect(strokes).toHaveLength(1);
    expect(strokes[0]?.points.some((point) => point.x < 0.5)).toBe(true);
    expect(strokes[0]?.points.some((point) => point.x > 0.5)).toBe(true);
  });
});

function touchAt(clientX: number, clientY: number): WechatMiniprogram.TouchEvent {
  const touch = { clientX, clientY, pageX: clientX, pageY: clientY, identifier: 1 };
  return {
    type: "touchmove",
    timeStamp: 1,
    target: { id: "capture", dataset: {}, offsetLeft: 0, offsetTop: 0 },
    currentTarget: { id: "capture", dataset: {}, offsetLeft: 0, offsetTop: 0 },
    detail: {},
    touches: [touch],
    changedTouches: [touch],
  };
}
