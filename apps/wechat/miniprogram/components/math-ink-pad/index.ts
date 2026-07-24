import type { InkStroke } from "../../api/math-ink";

interface Point {
  x: number;
  y: number;
  t: number;
}

interface TouchPoint {
  x?: number;
  y?: number;
  clientX?: number;
  clientY?: number;
}

interface InkRuntime {
  strokes: Point[][];
  currentStroke: Point[] | null;
  startedAt: number;
  canvas: WechatMiniprogram.Canvas | null;
  context: WechatMiniprogram.CanvasRenderingContext.CanvasRenderingContext2D | null;
  width: number;
  height: number;
}

const runtimes = new WeakMap<object, InkRuntime>();

function runtime(instance: object): InkRuntime {
  const value = runtimes.get(instance);
  if (!value) throw new Error("数学手写画布尚未初始化。");
  return value;
}

Component({
  properties: {
    recognizing: { type: Boolean, value: false },
  },
  data: {
    hasInk: false,
    canvasReady: false,
  },
  lifetimes: {
    attached() {
      runtimes.set(this, {
        strokes: [],
        currentStroke: null,
        startedAt: Date.now(),
        canvas: null,
        context: null,
        width: 0,
        height: 0,
      });
    },
    ready() {
      wx.createSelectorQuery()
        .in(this)
        .select("#mathInkCanvas")
        .fields({ node: true, size: true }, (raw) => {
          const result = raw as {
            node?: WechatMiniprogram.Canvas;
            width?: number;
            height?: number;
          };
          if (!result.node || !result.width || !result.height) return;
          const state = runtime(this);
          const pixelRatio = wx.getWindowInfo().pixelRatio;
          result.node.width = result.width * pixelRatio;
          result.node.height = result.height * pixelRatio;
          const context = result.node.getContext("2d");
          context.scale(pixelRatio, pixelRatio);
          context.strokeStyle = "#18322c";
          context.lineWidth = 3;
          context.lineCap = "round";
          context.lineJoin = "round";
          state.canvas = result.node;
          state.context = context;
          state.width = result.width;
          state.height = result.height;
          this.setData({ canvasReady: true });
        })
        .exec();
    },
    detached() {
      runtimes.delete(this);
    },
  },
  methods: {
    startStroke(event: WechatMiniprogram.TouchEvent) {
      const touch = event.touches[0];
      if (!touch) return;
      const state = runtime(this);
      if (!state.context) return;
      const point = this.point(touch);
      state.currentStroke = [point];
      state.strokes.push(state.currentStroke);
      this.setData({ hasInk: true });
    },
    moveStroke(event: WechatMiniprogram.TouchEvent) {
      const touch = event.touches[0];
      const stroke = runtime(this).currentStroke;
      if (!touch || !stroke) return;
      const point = this.point(touch);
      const previous = stroke.at(-1);
      if (!previous || Math.hypot(point.x - previous.x, point.y - previous.y) < 1) return;
      stroke.push(point);
      const context = runtime(this).context;
      if (!context) return;
      context.beginPath();
      context.moveTo(previous.x, previous.y);
      context.lineTo(point.x, point.y);
      context.stroke();
    },
    endStroke() {
      const state = runtime(this);
      if (state.currentStroke?.length === 1) {
        const point = state.currentStroke[0];
        if (point) state.currentStroke.push({ ...point, x: point.x + 0.5, t: point.t + 1 });
      }
      state.currentStroke = null;
    },
    clear() {
      const state = runtime(this);
      state.strokes = [];
      state.currentStroke = null;
      state.startedAt = Date.now();
      this.setData({ hasInk: false });
      state.context?.clearRect(0, 0, state.width, state.height);
      this.triggerEvent("clear");
    },
    recognize() {
      const strokes: InkStroke[] = runtime(this)
        .strokes.filter((stroke) => stroke.length >= 2)
        .map((stroke) => ({
          x: stroke.map((point) => Number(point.x.toFixed(2))),
          y: stroke.map((point) => Number(point.y.toFixed(2))),
          t: stroke.map((point) => point.t),
        }));
      if (!strokes.length) return;
      this.triggerEvent("recognize", { strokes });
    },
    point(touch: TouchPoint): Point {
      const state = runtime(this);
      return {
        x: touch.x ?? touch.clientX ?? 0,
        y: touch.y ?? touch.clientY ?? 0,
        t: Math.max(0, Date.now() - state.startedAt),
      };
    },
  },
});
