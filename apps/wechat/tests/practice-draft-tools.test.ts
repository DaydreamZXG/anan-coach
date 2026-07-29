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
  methods: {
    clearAll(this: AnnotationTestHost): Promise<void>;
    openMore(this: AnnotationTestHost): void;
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("practice drawable draft tools", () => {
  it("keeps only the obvious drawing modes without covering the question", async () => {
    const definition = await captureAnnotationComponent();

    expect(definition.data.tools.map((item) => item.value)).toEqual(["pan", "pen", "eraser"]);
    expect(definition.data.tools.map((item) => item.label)).toEqual(["滑动", "画笔", "橡皮"]);
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
  });
  await import("../miniprogram/task-runtime/annotation-layer/index");
  if (!captured) throw new Error("annotation layer was not registered");
  return captured;
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
