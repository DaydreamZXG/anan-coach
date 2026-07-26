import type { AnnotationStroke } from "../contracts";

/**
 * Figma 70:2/75:2 的笔迹层。
 * 笔迹坐标归一化到层宽高（0..1），与后端 AnnotationStroke 合同一致。
 */

const PEN_COLOR = "#4f39f6";
const HIGHLIGHTER_COLOR = "rgba(255, 205, 64, 0.4)";
const LASSO_COLOR = "rgba(79, 57, 246, 0.6)";
const SELECTED_COLOR = "rgba(251, 44, 54, 0.75)";
const PEN_WIDTH = 0.006;
const HIGHLIGHTER_WIDTH = 0.03;
const ERASE_DISTANCE = 0.03;
const MAX_STROKES = 120;

interface NormalizedPoint {
  x: number;
  y: number;
}

interface LayerRuntime {
  canvas: WechatMiniprogram.Canvas | null;
  context: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D | null;
  width: number;
  height: number;
  left: number;
  top: number;
  activeStroke: AnnotationStroke | null;
  lassoPath: NormalizedPoint[];
  selectedStrokeIds: Set<string>;
  strokeSequence: number;
}

const runtimes = new WeakMap<object, LayerRuntime>();

function runtime(instance: object): LayerRuntime {
  let value = runtimes.get(instance);
  if (!value) {
    value = {
      canvas: null,
      context: null,
      width: 0,
      height: 0,
      left: 0,
      top: 0,
      activeStroke: null,
      lassoPath: [],
      selectedStrokeIds: new Set(),
      strokeSequence: 0,
    };
    runtimes.set(instance, value);
  }
  return value;
}

interface AnnotationLayerHost {
  data: {
    mode: string;
    strokes: unknown;
    elapsedSeconds: number;
    replayMs: number;
    tool: string;
    lassoSelectedCount: number;
    drawnStrokeCount: number;
  };
  setData(patch: Record<string, unknown>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
}

function currentStrokes(host: AnnotationLayerHost): AnnotationStroke[] {
  return Array.isArray(host.data.strokes) ? (host.data.strokes as AnnotationStroke[]) : [];
}

function visibleStrokes(host: AnnotationLayerHost): AnnotationStroke[] {
  const strokes = currentStrokes(host);
  if (host.data.mode !== "replay") return strokes;
  return strokes.filter((stroke) => stroke.atMs <= host.data.replayMs);
}

function drawStroke(state: LayerRuntime, stroke: AnnotationStroke, selected: boolean): void {
  const context = state.context;
  if (!context || stroke.points.length < 2) return;
  context.save();
  context.lineCap = "round";
  context.lineJoin = "round";
  context.strokeStyle = selected
    ? SELECTED_COLOR
    : stroke.tool === "highlighter"
      ? HIGHLIGHTER_COLOR
      : PEN_COLOR;
  context.lineWidth = Math.max(1, stroke.width * state.width);
  context.beginPath();
  stroke.points.forEach((point, index) => {
    const x = point.x * state.width;
    const y = point.y * state.height;
    if (index === 0) context.moveTo(x, y);
    else context.lineTo(x, y);
  });
  context.stroke();
  context.restore();
}

function redraw(host: AnnotationLayerHost): void {
  const state = runtime(host);
  const context = state.context;
  if (!context) return;
  context.clearRect(0, 0, state.width, state.height);
  const strokes = visibleStrokes(host);
  // 供自动化核验：画布确实完成了几笔真实绘制。
  if (host.data.drawnStrokeCount !== strokes.length) {
    host.setData({ drawnStrokeCount: strokes.length });
  }
  for (const stroke of strokes) {
    drawStroke(state, stroke, state.selectedStrokeIds.has(stroke.strokeId));
  }
  if (state.activeStroke) drawStroke(state, state.activeStroke, false);
  if (state.lassoPath.length > 1 && host.data.tool === "lasso") {
    context.save();
    context.strokeStyle = LASSO_COLOR;
    context.lineWidth = 1.5;
    context.setLineDash([6, 4]);
    context.beginPath();
    state.lassoPath.forEach((point, index) => {
      const x = point.x * state.width;
      const y = point.y * state.height;
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    context.restore();
  }
}

function scheduleRedraw(host: AnnotationLayerHost, attempt = 0): void {
  if (host.data.mode === "hidden") return;
  wx.nextTick(() => {
    ensureCanvas(host, () => {
      redraw(host);
    });
    // 画布节点可能晚于首帧就绪，稍后再补一次首绘。
    if (attempt === 0) {
      setTimeout(() => {
        scheduleRedraw(host, 1);
      }, 300);
    }
  });
}

function ensureCanvas(host: AnnotationLayerHost, ready: () => void): void {
  const state = runtime(host);
  if (state.context) {
    ready();
    return;
  }
  const query = host.createSelectorQuery();
  query.select("#annotation-canvas").fields({ node: true, size: true, rect: true });
  query.exec(
    (
      results: Array<{
        node?: WechatMiniprogram.Canvas;
        width?: number;
        height?: number;
        left?: number;
        top?: number;
      } | null>,
    ) => {
      const node = results[0]?.node;
      const width = results[0]?.width ?? 0;
      const height = results[0]?.height ?? 0;
      if (!node || width <= 0 || height <= 0) return;
      const dpr = wx.getWindowInfo().pixelRatio;
      node.width = width * dpr;
      node.height = height * dpr;
      const context = node.getContext("2d");
      context.scale(dpr, dpr);
      state.canvas = node;
      state.context = context;
      state.width = width;
      state.height = height;
      state.left = results[0]?.left ?? 0;
      state.top = results[0]?.top ?? 0;
      ready();
    },
  );
}

function pointInPolygon(point: NormalizedPoint, polygon: NormalizedPoint[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i, i += 1) {
    const a = polygon[i];
    const b = polygon[j];
    if (!a || !b) continue;
    if (
      a.y > point.y !== b.y > point.y &&
      point.x < ((b.x - a.x) * (point.y - a.y)) / (b.y - a.y) + a.x
    ) {
      inside = !inside;
    }
  }
  return inside;
}

interface TouchPointLike {
  x?: number;
  y?: number;
  clientX?: number;
  clientY?: number;
}

function normalizedPoint(
  host: AnnotationLayerHost,
  event: WechatMiniprogram.TouchEvent,
): NormalizedPoint | null {
  const state = runtime(host);
  const touch = (event.touches[0] ?? event.changedTouches[0]) as TouchPointLike | undefined;
  if (!touch || state.width <= 0 || state.height <= 0) return null;
  const x = ((touch.clientX ?? touch.x ?? 0) - state.left) / state.width;
  const y = ((touch.clientY ?? touch.y ?? 0) - state.top) / state.height;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) };
}

Component({
  properties: {
    mode: { type: String, value: "hidden" },
    strokes: { type: null, value: [] },
    /** 当前题已作答秒数，用于给新笔迹盖 atMs 时间戳。 */
    elapsedSeconds: { type: Number, value: 0 },
    /** replay 模式：只画 atMs ≤ replayMs 的笔迹。 */
    replayMs: { type: Number, value: 0 },
  },
  data: {
    tool: "pen",
    tools: [
      { value: "pan", glyph: "✋", label: "滑动" },
      { value: "pen", glyph: "✎", label: "笔" },
      { value: "highlighter", glyph: "▆", label: "荧光" },
      { value: "eraser", glyph: "◫", label: "橡皮" },
      { value: "lasso", glyph: "◌", label: "套索" },
    ],
    lassoSelectedCount: 0,
    drawnStrokeCount: 0,
  },
  observers: {
    "mode, strokes, replayMs"(this: AnnotationLayerHost) {
      scheduleRedraw(this);
    },
  },
  lifetimes: {
    // 属性观察器不会为初始值触发；attach 后必须补一次首绘。
    attached() {
      scheduleRedraw(this);
    },
    detached() {
      runtimes.delete(this);
    },
  },
  methods: {
    selectTool(event: WechatMiniprogram.BaseEvent) {
      const state = runtime(this);
      state.lassoPath = [];
      state.selectedStrokeIds = new Set();
      this.setData({
        tool: String(event.currentTarget.dataset.tool ?? "pen"),
        lassoSelectedCount: 0,
      });
      ensureCanvas(this, () => {
        redraw(this);
      });
    },

    onTouchStart(event: WechatMiniprogram.TouchEvent) {
      const state = runtime(this);
      const point = normalizedPoint(this, event);
      if (!point) {
        ensureCanvas(this, () => {});
        return;
      }
      const tool = this.data.tool;
      if (tool === "pen" || tool === "highlighter") {
        state.strokeSequence += 1;
        state.activeStroke = {
          strokeId: `stroke-${String(this.data.elapsedSeconds)}-${String(state.strokeSequence)}-${Math.random().toString(36).slice(2, 8)}`,
          tool,
          width: tool === "highlighter" ? HIGHLIGHTER_WIDTH : PEN_WIDTH,
          points: [point],
          atMs: this.data.elapsedSeconds * 1000,
        };
      } else if (tool === "eraser") {
        this.eraseAt(point);
      } else if (tool === "lasso") {
        state.lassoPath = [point];
      }
    },

    onTouchMove(event: WechatMiniprogram.TouchEvent) {
      const state = runtime(this);
      const point = normalizedPoint(this, event);
      if (!point) return;
      const tool = this.data.tool;
      if ((tool === "pen" || tool === "highlighter") && state.activeStroke) {
        if (state.activeStroke.points.length < 240) state.activeStroke.points.push(point);
        redraw(this);
      } else if (tool === "eraser") {
        this.eraseAt(point);
      } else if (tool === "lasso") {
        state.lassoPath.push(point);
        redraw(this);
      }
    },

    onTouchEnd() {
      const state = runtime(this);
      const tool = this.data.tool;
      if ((tool === "pen" || tool === "highlighter") && state.activeStroke) {
        const stroke = state.activeStroke;
        state.activeStroke = null;
        if (stroke.points.length >= 2) {
          const strokes = currentStrokes(this);
          if (strokes.length >= MAX_STROKES) {
            void wx.showToast({ title: "批注笔画已达上限", icon: "none" });
            redraw(this);
            return;
          }
          this.triggerEvent("strokeschange", {
            strokes: [...strokes, stroke],
            action: "added",
          });
        }
      } else if (tool === "lasso" && state.lassoPath.length > 2) {
        const selected = new Set<string>();
        for (const stroke of currentStrokes(this)) {
          if (stroke.points.every((point) => pointInPolygon(point, state.lassoPath))) {
            selected.add(stroke.strokeId);
          }
        }
        state.selectedStrokeIds = selected;
        state.lassoPath = [];
        this.setData({ lassoSelectedCount: selected.size });
        redraw(this);
      }
    },

    eraseAt(point: NormalizedPoint) {
      const strokes = currentStrokes(this);
      const remaining = strokes.filter(
        (stroke) =>
          !stroke.points.some(
            (candidate) =>
              Math.hypot(candidate.x - point.x, candidate.y - point.y) < ERASE_DISTANCE,
          ),
      );
      if (remaining.length !== strokes.length) {
        this.triggerEvent("strokeschange", { strokes: remaining, action: "erased" });
      }
    },

    deleteLassoSelection() {
      const state = runtime(this);
      const strokes = currentStrokes(this).filter(
        (stroke) => !state.selectedStrokeIds.has(stroke.strokeId),
      );
      state.selectedStrokeIds = new Set();
      this.setData({ lassoSelectedCount: 0 });
      this.triggerEvent("strokeschange", { strokes, action: "erased" });
    },

    undo() {
      const strokes = currentStrokes(this);
      if (strokes.length === 0) return;
      this.triggerEvent("strokeschange", {
        strokes: strokes.slice(0, -1),
        action: "erased",
      });
    },

    finish() {
      this.triggerEvent("done");
    },
  },
});
