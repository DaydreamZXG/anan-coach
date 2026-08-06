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
  left: number;
  top: number;
}

const runtimes = new WeakMap<object, InkRuntime>();

function runtime(instance: object): InkRuntime {
  const value = runtimes.get(instance);
  if (!value) throw new Error("数学手写画布尚未初始化。");
  return value;
}

/**
 * 确保画布节点就绪并返回 2d context。
 * ui-sheet 懒挂载 + 关闭即销毁子树，画布节点可能晚于 ready() 首帧就绪，
 * 且收起→重开会重建组件。查询失败或节点未挂载时静默保留，由调用方
 * （startStroke / scheduleRedraw）再次触发重查，避免"第二题画不出来"。
 */
function ensureCanvas(host: InkHost, ready: () => void): void {
  const state = runtime(host);
  if (state.context) {
    ready();
    return;
  }
  const query = host.createSelectorQuery();
  query.select("#mathInkCanvas").fields({ node: true, size: true, rect: true });
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
      const pixelRatio = wx.getWindowInfo().pixelRatio;
      node.width = width * pixelRatio;
      node.height = height * pixelRatio;
      const context = node.getContext("2d");
      context.scale(pixelRatio, pixelRatio);
      context.strokeStyle = "#18322c";
      context.lineWidth = 3;
      context.lineCap = "round";
      context.lineJoin = "round";
      state.canvas = node;
      state.context = context;
      state.width = width;
      state.height = height;
      state.left = results[0]?.left ?? 0;
      state.top = results[0]?.top ?? 0;
      host.setData({ canvasReady: true });
      ready();
    },
  );
}

interface InkHost {
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
  setData(data: Partial<{ canvasReady: boolean }>): void;
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
        left: 0,
        top: 0,
      });
    },
    ready() {
      runtimes.set(this, {
        strokes: [],
        currentStroke: null,
        startedAt: Date.now(),
        canvas: null,
        context: null,
        width: 0,
        height: 0,
        left: 0,
        top: 0,
      });
      // 画布节点可能晚于首帧就绪；先查一次，失败则 300ms 后补查。
      wx.nextTick(() => {
        ensureCanvas(this, () => {});
      });
      setTimeout(() => {
        ensureCanvas(this, () => {});
      }, 300);
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
      if (!state.context) {
        // 画布尚未就绪：重查一次；仍失败则静默返回，由后续触摸重试。
        ensureCanvas(this, () => {});
        return;
      }
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
      // 相对画布左上角的坐标，避免画布不在屏幕原点时笔迹整体偏移。
      const x = (touch.clientX ?? touch.x ?? 0) - state.left;
      const y = (touch.clientY ?? touch.y ?? 0) - state.top;
      return {
        x,
        y,
        t: Math.max(0, Date.now() - state.startedAt),
      };
    },
  },
});
