import type { AnnotationStroke } from "../../../miniprogram/task-runtime/contracts";
import { arrayPropertyItems } from "../component-property";
import {
  capturedHorizontalX,
  horizontalRegionAtPoint,
  normalizeSegmentRects,
  renderedHorizontalX,
  resolveStrokeAnchor,
  type AnchorSegmentRect,
  type HorizontalAnnotationRegion,
  type NormalizedSegmentRect,
} from "./anchor";
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
 *
 * 坐标体系（内容坐标系，笔记 App 成熟做法）：
 * - 笔迹点归一化到「题目内容」宽高（0..1），滚动不改变笔迹坐标 → 持久化永远合法、
 *   永不越界成负数、不产生滚动位移补偿的任何 bug。
 * - 绘制时按当前 scrollTop 换算到视口：drawY = p.y * contentHeight - scrollTop。
 * - 触笔时反向换算：contentY = (clientY - canvasTop + scrollTop) / contentHeight。
 * - 句段表同样存内容坐标，命中在内容空间完成，滚动后天然一致。
 * - static（已完成涂画展示）/ annotate 共用同一绘制，滚动时笔迹天然跟随，无需重测。
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
  /**
   * 契约增量 v2：题干句段表（内容坐标系，滚动恒定）。命中在内容空间完成，
   * 滚动后依然一致，不需要滚动补偿。缺省空表 → 笔迹照常绘制但不做锚定。
   */
  segments: NormalizedSegmentRect[];
  horizontalRegions: HorizontalAnnotationRegion[];
  /** 题目内容总高（px），来自宿主；笔迹/句段的内容坐标换算基准。 */
  contentHeight: number;
  /** 当前滚动位置（px），只影响绘制换算，不改变任何存储坐标。 */
  scrollTop: number;
  /** 上一次观察到的组件模式，用于识别重新打开草稿。 */
  lastMode: string;
  /** 合并同一帧内的多次重绘请求，避免滚动时反复刷画。 */
  redrawQueued: boolean;
  /** selectorQuery 在飞时不再启动重复查询。 */
  canvasQueryPending: boolean;
  /** 仅在首次画布节点尚未就绪时保留一次补绘。 */
  redrawRetryTimer: ReturnType<typeof setTimeout> | null;
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
      segments: [],
      horizontalRegions: [],
      contentHeight: 0,
      scrollTop: 0,
      lastMode: "hidden",
      redrawQueued: false,
      canvasQueryPending: false,
      redrawRetryTimer: null,
    };
    runtimes.set(instance, value);
  }
  return value;
}

function disposeRuntime(instance: object): void {
  const state = runtimes.get(instance);
  if (state && state.redrawRetryTimer !== null) {
    clearTimeout(state.redrawRetryTimer);
  }
  runtimes.delete(instance);
}

/**
 * 横竖屏切换会改变 canvas 的节点、逻辑尺寸和页面坐标。笔迹本身使用内容
 * 归一化坐标，可以安全保留；仅废弃与旧布局绑定的运行时对象，下一帧重新测量。
 */
function invalidateCanvasLayout(host: AnnotationLayerHost): void {
  const state = runtime(host);
  state.canvas = null;
  state.context = null;
  state.width = 0;
  state.height = 0;
  state.left = 0;
  state.top = 0;
  // 旋转发生在触摸手势中间时，旧触点不应跨布局继续成笔。
  state.activeStroke = null;
  state.activeStrokeStartedAt = 0;
  state.lassoPath = [];
  state.contentHeight = effectiveContentHeight(host, state.height);
  state.scrollTop = host.data.coordinateSpace === "viewport" ? 0 : host.data.scrollTop;
}

interface AnnotationLayerHost {
  data: {
    mode: string;
    strokes: unknown;
    elapsedSeconds: number;
    elapsedMs: number;
    replayMs: number;
    segments: unknown;
    horizontalRegions: unknown;
    scrollTop: number;
    contentHeight: number;
    coordinateSpace: string;
    tool: string;
    toolbarCollapsed: boolean;
    lassoSelectedCount: number;
    drawnStrokeCount: number;
    strokePrefix: string;
    surfaceSwitchAvailable: boolean;
    activeSurface: string;
    toolbarAvailable: boolean;
    activeTool: string;
    scopeLabel: string;
  };
  setData(patch: Record<string, unknown>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
}

function effectiveContentHeight(host: AnnotationLayerHost, viewportHeight: number): number {
  return host.data.coordinateSpace === "viewport" ? viewportHeight : host.data.contentHeight;
}

function currentStrokes(host: AnnotationLayerHost): AnnotationStroke[] {
  return arrayPropertyItems<AnnotationStroke>(host.data.strokes);
}

function normalizeAnnotationTool(value: string): "pan" | "pen" | "highlighter" | "eraser" {
  return value === "pen" || value === "highlighter" || value === "eraser" ? value : "pan";
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

/** 内容坐标 → 视口坐标（绘制用）；y 减去滚动偏移。 */
function contentToViewport(state: LayerRuntime, y: number): number {
  return y * state.contentHeight - state.scrollTop;
}

function drawStroke(state: LayerRuntime, stroke: AnnotationStroke, selected: boolean): void {
  const context = state.context;
  if (!context || stroke.points.length < 2 || state.contentHeight <= 0) return;
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
  const horizontalRegion = stroke.anchor
    ? state.horizontalRegions.find((region) => region.blockId === stroke.anchor?.blockId)
    : undefined;
  stroke.points.forEach((point, index) => {
    const x = horizontalRegion
      ? renderedHorizontalX(horizontalRegion, state, point.x)
      : point.x * state.width;
    const y = contentToViewport(state, point.y);
    // 完全在视口外的段直接跳过，减少滚动时的绘制成本。
    if (y < -state.height || y > state.height * 2) return;
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
      const y = contentToViewport(state, point.y);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    context.restore();
  }
}

function scheduleRedraw(host: AnnotationLayerHost, retryIfMissing = false): void {
  if (host.data.mode === "hidden") return;
  const state = runtime(host);
  if (!state.redrawQueued) {
    state.redrawQueued = true;
    wx.nextTick(() => {
      const current = runtimes.get(host);
      if (!current) return;
      current.redrawQueued = false;
      if (host.data.mode === "hidden") return;
      ensureCanvas(host, () => {
        redraw(host);
      });
    });
  }
  // 画布节点可能晚于首帧就绪；只有首绘/布局变化需要一次容错补绘。
  // 滚动重绘不得挂延时任务，否则每一帧都会再画一次并引起卡顿。
  if (retryIfMissing && !state.context && state.redrawRetryTimer === null) {
    state.redrawRetryTimer = setTimeout(() => {
      const current = runtimes.get(host);
      if (!current) return;
      current.redrawRetryTimer = null;
      if (host.data.mode === "hidden" || current.context) return;
      scheduleRedraw(host);
    }, 300);
  }
}

/**
 * 契约增量 v2：把宿主上报的句段视口矩形归一化到内容坐标系并整表替换。
 * 只在 segments / contentHeight 变化时执行（测量时刻的 scrollTop 一并计入）；
 * 纯滚动不重跑 —— 内容坐标滚动恒定，重跑反而会把当前 scrollTop 重复计入。
 */
function syncSegments(host: AnnotationLayerHost): void {
  const state = runtime(host);
  const raw = arrayPropertyItems<AnchorSegmentRect>(host.data.segments);
  if (raw.length === 0 || state.contentHeight <= 0) {
    state.segments = [];
    return;
  }
  const next = normalizeSegmentRects(raw, state, host.data.scrollTop, state.contentHeight);
  state.segments = next.length > 0 ? next : [];
}

function ensureCanvas(host: AnnotationLayerHost, ready: () => void): void {
  const state = runtime(host);
  if (state.context) {
    ready();
    return;
  }
  if (state.canvasQueryPending) return;
  state.canvasQueryPending = true;
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
      // hidden/detached 后旧查询可能才回调，不得把旧 canvas 绑回新会话。
      if (runtimes.get(host) !== state || host.data.mode === "hidden") return;
      state.canvasQueryPending = false;
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
      state.contentHeight = effectiveContentHeight(host, height);
      state.scrollTop = host.data.coordinateSpace === "viewport" ? 0 : host.data.scrollTop;
      // 画布尺寸就绪后按当前句段表重算内容坐标（此前可能因尺寸未就绪失败）。
      syncSegments(host);
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

/** 视口触点 → 内容坐标（0..1 相对内容宽高）。滚动只加一个 scrollTop，坐标恒定。 */
function contentPoint(
  host: AnnotationLayerHost,
  event: WechatMiniprogram.TouchEvent,
  activeHorizontalBlockId?: string,
): { point: NormalizedPoint; horizontalRegion: HorizontalAnnotationRegion | null } | null {
  const state = runtime(host);
  const touch = (event.touches[0] ?? event.changedTouches[0]) as TouchPointLike | undefined;
  if (!touch || state.width <= 0 || state.contentHeight <= 0) return null;
  // TouchEvent 的 client 坐标和 selectorQuery.rect 都以可视区域为基准。
  // 不能使用自定义组件 currentTarget.offset*：它是组件局部口径，平板竖屏
  // 会把整笔平移。旋转后的 rect 由 resize 生命周期重新测量。
  const clientX = touch.clientX ?? touch.x ?? 0;
  const clientY = touch.clientY ?? touch.y ?? 0;
  const horizontalRegion = activeHorizontalBlockId
    ? (state.horizontalRegions.find((region) => region.blockId === activeHorizontalBlockId) ?? null)
    : horizontalRegionAtPoint(state.horizontalRegions, state, clientX, clientY);
  const x = horizontalRegion
    ? capturedHorizontalX(horizontalRegion, clientX)
    : (clientX - state.left) / state.width;
  const y = (clientY - state.top + state.scrollTop) / state.contentHeight;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return {
    point: { x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) },
    horizontalRegion,
  };
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
    /**
     * 契约增量 v2：题干句段的视口矩形表（像素，宿主 content-renderer 测量）。
     * 归一化到内容坐标系后做锚定命中；滚动恒定，无需重测。
     */
    segments: { type: null, value: [] },
    /**
     * 契约增量 v2：题干滚动区的 scrollTop（像素）。只参与绘制换算
     * （drawY = contentY * contentHeight - scrollTop），不改变存储坐标。
     */
    scrollTop: { type: Number, value: 0 },
    /** 契约增量 v2：题目内容总高（像素），内容坐标系换算基准。 */
    contentHeight: { type: Number, value: 0 },
    /** 035：viewport 表示整块可见工作区就是草稿坐标系，滚动不平移笔迹。 */
    coordinateSpace: { type: String, value: "content" },
    /** 表格横滚视口；仅影响 x 换算，持久化仍沿用 AnnotationPoint。 */
    horizontalRegions: { type: null, value: [] },
    /** 多内容面共享同一题的 annotations 时，用前缀保证笔迹 ID 与所属画布稳定。 */
    strokePrefix: { type: String, value: "stroke" },
    /** 一个草稿入口在题面与共享材料之间切换；仅控制入口，不合并两种笔迹坐标系。 */
    surfaceSwitchAvailable: { type: Boolean, value: false },
    activeSurface: { type: String, value: "question" },
    /** 连续草稿由宿主指定哪一块画布承载唯一工具栏。 */
    toolbarAvailable: { type: Boolean, value: true },
    /** 多画布只共享临时工具，不共享笔迹坐标或持久化 reducer。 */
    activeTool: { type: String, value: "pan" },
    /** 整屏模式向用户说明当前笔迹归属，避免误以为会跨题共享。 */
    scopeLabel: { type: String, value: "" },
  },
  data: {
    // 打开草稿先保持题面可滚动、选项可点；用户明确选画笔后才挂载全屏作画层。
    tool: "pan",
    toolbarCollapsed: false,
    tools: [
      { value: "pan", glyph: "✋", label: "滑动" },
      { value: "pen", glyph: "✎", label: "画笔" },
      // 契约增量 v2：自由画荧光笔，命中句段后给笔迹盖 anchor，AI 可见"标记了哪句"。
      { value: "highlighter", glyph: "🖍", label: "荧光笔" },
      { value: "eraser", glyph: "◫", label: "橡皮" },
    ],
    lassoSelectedCount: 0,
    drawnStrokeCount: 0,
  },
  observers: {
    "mode, strokes, replayMs"(this: AnnotationLayerHost) {
      const state = runtime(this);
      const enteringAnnotate = this.data.mode === "annotate" && state.lastMode !== "annotate";
      state.lastMode = this.data.mode;
      // 新开/重新进入草稿一律先回到滑动模式。这样不会因上次停在画笔、
      // 草稿有历史笔迹而恢复全屏触摸盾，导致题面和选项持续失去交互。
      if (
        enteringAnnotate &&
        (this.data.tool !== "pan" || this.data.toolbarCollapsed || this.data.lassoSelectedCount > 0)
      ) {
        this.setData({ toolbarCollapsed: false, tool: "pan", lassoSelectedCount: 0 });
      }
      // wx:if 会在 hidden 时销毁 canvas 节点。WeakMap 中的 context 不会
      // 自动感知节点已卸载，若继续复用就会把下一题的笔迹画到旧节点。
      // 隐藏时丢弃整个运行时，下次 annotate 必须重查节点并重建 context。
      if (this.data.mode === "hidden") {
        disposeRuntime(this);
        return;
      }
      scheduleRedraw(this, true);
    },
    // 句段/内容高变化：整表换算一次（内容坐标恒定）。纯滚动不触发本分支。
    "segments, contentHeight, coordinateSpace"(this: AnnotationLayerHost) {
      const state = runtime(this);
      state.contentHeight = effectiveContentHeight(this, state.height);
      state.scrollTop = this.data.coordinateSpace === "viewport" ? 0 : this.data.scrollTop;
      if (this.data.mode !== "hidden") {
        syncSegments(this);
        scheduleRedraw(this);
      }
    },
    horizontalRegions(this: AnnotationLayerHost) {
      runtime(this).horizontalRegions = arrayPropertyItems<HorizontalAnnotationRegion>(
        this.data.horizontalRegions,
      );
      scheduleRedraw(this);
    },
    // 滚动只更新绘制偏移，重绘即可（同步、零重测、零坐标写入）。
    scrollTop(this: AnnotationLayerHost) {
      runtime(this).scrollTop = this.data.coordinateSpace === "viewport" ? 0 : this.data.scrollTop;
      scheduleRedraw(this);
    },
    activeTool(this: AnnotationLayerHost) {
      const next = normalizeAnnotationTool(this.data.activeTool);
      if (next === this.data.tool) return;
      const state = runtime(this);
      state.activeStroke = null;
      state.activeStrokeStartedAt = 0;
      state.lassoPath = [];
      state.selectedStrokeIds = new Set();
      this.setData({ tool: next, lassoSelectedCount: 0 });
      scheduleRedraw(this);
    },
  },
  lifetimes: {
    // 属性观察器不会为初始值触发；attach 后必须补一次首绘。
    attached() {
      runtime(this).horizontalRegions = arrayPropertyItems<HorizontalAnnotationRegion>(
        this.data.horizontalRegions,
      );
      scheduleRedraw(this, true);
    },
    detached() {
      disposeRuntime(this);
    },
  },
  pageLifetimes: {
    resize(this: AnnotationLayerHost) {
      if (this.data.mode === "hidden") return;
      invalidateCanvasLayout(this);
      scheduleRedraw(this, true);
      // 部分安卓平板的 resize 事件早于双栏重排完成；布局稳定后再校准一次，
      // 但不改任何笔迹数据。触点继续使用同为视口口径的 client 坐标与 rect。
      setTimeout(() => {
        if (this.data.mode === "hidden") return;
        invalidateCanvasLayout(this);
        scheduleRedraw(this, true);
      }, 120);
    },
  },
  methods: {
    collapseToolbar() {
      const state = runtime(this);
      state.activeStroke = null;
      state.activeStrokeStartedAt = 0;
      state.lassoPath = [];
      state.selectedStrokeIds = new Set();
      // 收起后必须切到滑动：隐藏的作画捕获层不能继续挡住题面滚动。
      this.setData({
        tool: "pan",
        toolbarCollapsed: true,
        lassoSelectedCount: 0,
      });
      redraw(this);
    },

    expandToolbar() {
      this.setData({ toolbarCollapsed: false });
    },

    selectTool(event: WechatMiniprogram.BaseEvent) {
      const state = runtime(this);
      state.lassoPath = [];
      state.selectedStrokeIds = new Set();
      const tool = normalizeAnnotationTool(String(event.currentTarget.dataset.tool ?? "pan"));
      this.setData({
        tool,
        lassoSelectedCount: 0,
      });
      this.triggerEvent("toolchange", { tool });
      ensureCanvas(this, () => {
        redraw(this);
      });
    },

    switchSurface(event: WechatMiniprogram.BaseEvent) {
      const surface = String(event.currentTarget.dataset.surface ?? "");
      if (surface !== "question" && surface !== "material") return;
      if (surface === this.data.activeSurface) return;
      this.triggerEvent("surfacechange", { surface });
    },

    onTouchStart(event: WechatMiniprogram.TouchEvent) {
      const state = runtime(this);
      if (this.data.tool !== "pan") this.triggerEvent("focus");
      const touch = contentPoint(this, event);
      if (!touch) {
        ensureCanvas(this, () => {});
        return;
      }
      const { point, horizontalRegion } = touch;
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
          strokeId: `${this.data.strokePrefix || "stroke"}-${String(this.data.elapsedSeconds)}-${String(state.strokeSequence)}-${Math.random().toString(36).slice(2, 8)}`,
          tool,
          width: tool === "highlighter" ? HIGHLIGHTER_WIDTH : PEN_WIDTH,
          points: [capturedPoint(point, 0, pressure)],
          atMs: nowMs(this),
          ...(horizontalRegion
            ? {
                anchor: {
                  blockId: horizontalRegion.blockId,
                  segmentIndex: 0,
                  segmentText: horizontalRegion.label || "资料表格",
                },
              }
            : {}),
        };
      } else if (tool === "eraser") {
        this.eraseAt(point, horizontalRegion);
      } else if (tool === "lasso") {
        state.lassoPath = [point];
      }
    },

    onTouchMove(event: WechatMiniprogram.TouchEvent) {
      const state = runtime(this);
      const touch = contentPoint(this, event, state.activeStroke?.anchor?.blockId);
      if (!touch) return;
      const { point, horizontalRegion } = touch;
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
        this.eraseAt(point, horizontalRegion);
      } else if (tool === "lasso") {
        state.lassoPath.push(point);
        redraw(this);
      }
    },

    onTouchEnd() {
      const state = runtime(this);
      const tool = this.data.tool;
      if ((tool === "pen" || tool === "highlighter") && state.activeStroke) {
        let stroke = state.activeStroke;
        state.activeStroke = null;
        // 真机上的点、短横和汉字收笔经常只有 touchstart/touchend，没有 touchmove。
        // 服务端契约要求至少两个点，因此补一个几乎重合的尾点，既保留用户这一笔，
        // 也不放宽后端数据约束。
        if (stroke.points.length === 1) {
          const first = stroke.points[0];
          if (first) {
            stroke = {
              ...stroke,
              points: [
                first,
                {
                  ...first,
                  x:
                    first.x >= 0.9999
                      ? Math.max(0, first.x - 0.0001)
                      : Math.min(1, first.x + 0.0001),
                  t: Math.max(1, first.t ?? 0),
                },
              ],
            };
          }
        }
        if (stroke.points.length >= 2) {
          // 契约增量 v2：内容空间最近句段命中 → 给笔画盖 anchor（AI 可见"标记了哪句"）。
          // 只存契约字段（blockId/segmentIndex/segmentText）。
          const anchor = stroke.anchor ? null : resolveStrokeAnchor(stroke, state.segments);
          const anchored = anchor
            ? {
                ...stroke,
                anchor: {
                  blockId: anchor.blockId,
                  segmentIndex: anchor.segmentIndex,
                  segmentText: anchor.segmentText,
                },
              }
            : stroke;
          // 契约增量 v1：120 笔上限含墓碑；满员时物理删除最旧的已擦除笔画腾位。
          const strokes = appendStrokeWithinLimit(currentStrokes(this), anchored);
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

    eraseAt(point: NormalizedPoint, horizontalRegion: HorizontalAnnotationRegion | null) {
      // 契约增量 v1：橡皮不物理删除，命中的活笔画盖 erasedAtMs 墓碑。
      // 内容坐标统一，命中与滚动无关。
      const state = runtime(this);
      const strokes = currentStrokes(this);
      const next = tombstoneStrokes(
        strokes,
        (stroke) => {
          const strokeHorizontalRegion = stroke.anchor
            ? state.horizontalRegions.find((region) => region.blockId === stroke.anchor?.blockId)
            : undefined;
          if ((strokeHorizontalRegion?.blockId ?? null) !== (horizontalRegion?.blockId ?? null)) {
            return false;
          }
          return stroke.points.some(
            (candidate) =>
              Math.hypot(candidate.x - point.x, candidate.y - point.y) < ERASE_DISTANCE,
          );
        },
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
