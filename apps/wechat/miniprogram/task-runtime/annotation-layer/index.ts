import type { AnnotationStroke } from "../contracts";
import {
  appendStrokeWithinLimit,
  capturedPoint,
  liveStrokes,
  normalizedPressure,
  shouldRejectAsPalm,
  strokesVisibleAtReplay,
  tombstoneStrokes,
} from "./strokes";

/**
 * Figma 70:2/75:2 的笔迹层。
 * 笔迹坐标归一化到层宽高（0..1），与后端 AnnotationStroke 合同一致。
 * 契约增量 v1：逐点带相对起笔的毫秒偏移 t 与可选归一化压感 p；
 * 擦除只盖 erasedAtMs 墓碑。
 */

const PEN_COLOR = "#4f39f6";
const HIGHLIGHTER_COLOR = "rgba(255, 205, 64, 0.4)";
const LASSO_COLOR = "rgba(79, 57, 246, 0.6)";
const SELECTED_COLOR = "rgba(251, 44, 54, 0.75)";
const PEN_WIDTH = 0.006;
const HIGHLIGHTER_WIDTH = 0.03;
const ERASE_DISTANCE = 0.03;

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
  /** 契约增量 v1：本笔起笔的 Date.now() 基准，用于逐点毫秒偏移 t。 */
  activeStrokeStartedAt: number;
  /**
   * 手掌误触抑制的先例标志：本次批注会话内是否出现过 force>0 的笔画起笔。
   * detached（runtimes.delete）或退出批注模式（mode 变 hidden）时重置。
   */
  sessionSawPressure: boolean;
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
      activeStrokeStartedAt: 0,
      sessionSawPressure: false,
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
    elapsedMs: number;
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

/** 本题活跃毫秒时钟；宿主没有传 elapsedMs 时退回 elapsedSeconds 的秒级精度。 */
function nowMs(host: AnnotationLayerHost): number {
  return host.data.elapsedMs >= 0 ? host.data.elapsedMs : host.data.elapsedSeconds * 1000;
}

function visibleStrokes(host: AnnotationLayerHost): AnnotationStroke[] {
  const strokes = currentStrokes(host);
  // 契约增量 v1：作答态隐藏墓碑；回放态还原擦除过程（replayMs >= erasedAtMs 时消失）。
  if (host.data.mode !== "replay") return liveStrokes(strokes);
  return strokesVisibleAtReplay(strokes, host.data.replayMs);
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
  /** 契约增量 v1：iOS 压感触点的按压力度（Apple Pencil 满压约 3.3333）。 */
  force?: number;
}

/** 契约增量 v1：读当前触点的 touch.force（安卓/无压感设备为缺省或 0）。 */
function touchForce(event: WechatMiniprogram.TouchEvent): number | undefined {
  const touch = (event.touches[0] ?? event.changedTouches[0]) as TouchPointLike | undefined;
  return touch?.force;
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
    /** 契约增量 v1：本题活跃毫秒时钟；<0 表示宿主未提供，退回 elapsedSeconds。 */
    elapsedMs: { type: Number, value: -1 },
    /** replay 模式：只画 atMs ≤ replayMs 且尚未走到 erasedAtMs 的笔迹。 */
    replayMs: { type: Number, value: 0 },
  },
  data: {
    tool: "pen",
    tools: [
      { value: "pan", glyph: "✋", label: "滑动" },
      { value: "pen", glyph: "✎", label: "画笔" },
      { value: "eraser", glyph: "◫", label: "橡皮" },
    ],
    lassoSelectedCount: 0,
    drawnStrokeCount: 0,
  },
  observers: {
    "mode, strokes, replayMs"(this: AnnotationLayerHost) {
      // 退出批注模式即会话结束：重置手掌抑制的压感先例标志
      //（detached 时 runtimes.delete 已整体重置）。
      if (this.data.mode === "hidden") {
        runtime(this).sessionSawPressure = false;
      }
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
        // 契约增量 v1：读 touch.force 采集压感（iOS 满压 3.3333 归一到 (0..1]）。
        const pressure = normalizedPressure(touchForce(event));
        // 手掌误触抑制（启发式边界）：会话内出现过 force>0 的起笔即认定在用压感笔，
        // 此后 force===0 的新触点按手掌搁置忽略该笔；无 Pencil 的 iPad 用手指书写
        // 不受影响 —— 手指不会产生 force>0 的先例。detached/退出批注模式时重置。
        if (shouldRejectAsPalm(state.sessionSawPressure, pressure)) return;
        if (pressure !== undefined) state.sessionSawPressure = true;
        state.strokeSequence += 1;
        // 契约增量 v1：起笔以 Date.now() 为基准，逐点 t 为相对起笔的毫秒偏移。
        state.activeStrokeStartedAt = Date.now();
        state.activeStroke = {
          strokeId: `stroke-${String(this.data.elapsedSeconds)}-${String(state.strokeSequence)}-${Math.random().toString(36).slice(2, 8)}`,
          tool,
          width: tool === "highlighter" ? HIGHLIGHTER_WIDTH : PEN_WIDTH,
          points: [capturedPoint(point, 0, pressure)],
          atMs: nowMs(this),
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
        if (state.activeStroke.points.length < 240) {
          const lastT = state.activeStroke.points[state.activeStroke.points.length - 1]?.t ?? 0;
          // 逐点偏移非降序：以起笔 Date.now() 基准计算，并夹到不小于上一点。
          const t = Math.max(lastT, Date.now() - state.activeStrokeStartedAt);
          state.activeStroke.points.push(
            capturedPoint(point, t, normalizedPressure(touchForce(event))),
          );
        }
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
          // 契约增量 v1：120 笔上限含墓碑；满员时物理删除最旧的已擦除笔画腾位。
          const strokes = appendStrokeWithinLimit(currentStrokes(this), stroke);
          if (!strokes) {
            void wx.showToast({ title: "草稿笔画已达上限", icon: "none" });
            redraw(this);
            return;
          }
          this.triggerEvent("strokeschange", { strokes, action: "added" });
        }
      } else if (tool === "lasso" && state.lassoPath.length > 2) {
        const selected = new Set<string>();
        for (const stroke of liveStrokes(currentStrokes(this))) {
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
      // 契约增量 v1：橡皮不物理删除，命中的活笔画盖 erasedAtMs 墓碑。
      const strokes = currentStrokes(this);
      const next = tombstoneStrokes(
        strokes,
        (stroke) =>
          stroke.points.some(
            (candidate) =>
              Math.hypot(candidate.x - point.x, candidate.y - point.y) < ERASE_DISTANCE,
          ),
        nowMs(this),
      );
      if (next !== strokes) {
        this.triggerEvent("strokeschange", { strokes: next, action: "erased" });
      }
    },

    deleteLassoSelection() {
      const state = runtime(this);
      const strokes = currentStrokes(this);
      const next = tombstoneStrokes(
        strokes,
        (stroke) => state.selectedStrokeIds.has(stroke.strokeId),
        nowMs(this),
      );
      state.selectedStrokeIds = new Set();
      this.setData({ lassoSelectedCount: 0 });
      if (next !== strokes) {
        this.triggerEvent("strokeschange", { strokes: next, action: "erased" });
      } else {
        redraw(this);
      }
    },

    undo() {
      const strokes = currentStrokes(this);
      const lastLive = liveStrokes(strokes).pop();
      if (!lastLive) return;
      this.triggerEvent("strokeschange", {
        strokes: tombstoneStrokes(strokes, (stroke) => stroke === lastLive, nowMs(this)),
        action: "erased",
      });
    },

    async clearAll() {
      const strokes = currentStrokes(this);
      if (liveStrokes(strokes).length === 0) {
        void wx.showToast({ title: "草稿还是空的", icon: "none" });
        return;
      }
      const decision = await wx.showModal({
        title: "清空草稿？",
        content: "本题已经画下的内容会被清空。",
        confirmText: "清空",
        confirmColor: "#b0404c",
        cancelText: "保留",
      });
      if (!decision.confirm) return;
      const state = runtime(this);
      state.activeStroke = null;
      state.lassoPath = [];
      state.selectedStrokeIds = new Set();
      this.setData({ lassoSelectedCount: 0 });
      this.triggerEvent("strokeschange", {
        // 仍写入擦除墓碑，保留 annotations/action evidence 的完整事件轨迹。
        strokes: tombstoneStrokes(strokes, () => true, nowMs(this)),
        action: "erased",
      });
    },

    openMore() {
      this.triggerEvent("more");
    },

    finish() {
      this.triggerEvent("done");
    },
  },
});
