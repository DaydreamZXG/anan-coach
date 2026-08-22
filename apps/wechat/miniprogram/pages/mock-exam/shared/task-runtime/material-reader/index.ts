import {
  contentAnchorDomId,
  nextContentLineSpacing,
  type ContentDisplayPreferences,
} from "../content-renderer/model";
import { measureSystemChrome } from "../../../../../components/safe-top/metrics";
import type { HorizontalAnnotationRegion } from "../annotation-layer/anchor";
import {
  createMaterialReaderState,
  cycleFontScale,
  nextFontScale,
  reduceMaterialReader,
  restoreMaterialReaderState,
  type MaterialReaderEvent,
  type MaterialReaderMode,
  type MaterialReaderPosition,
  type MaterialReaderState,
} from "./state";

interface MaterialReaderData {
  appearance: string;
  blocks: unknown;
  inlineHeaderAvailable: boolean;
  anchors: unknown;
  state: MaterialReaderState;
  title: string;
  subtitle: string;
  chipLabel: string;
  returnLabel: string;
  annotateAvailable: boolean;
  annotateActive: boolean;
  interactionActive: boolean;
  readerMode: MaterialReaderMode;
  position: MaterialReaderPosition;
  preferences: ContentDisplayPreferences;
  scrollIntoView: string;
  zoomPercent: number;
  fontPercent: number;
  eyeCare: boolean;
  statusBarHeight: number;
  capsuleReserve: number;
  readPercent: number;
  annotationOpen: boolean;
  annotationStrokes: unknown;
  annotationElapsedSeconds: number;
  annotationElapsedMs: number;
  annotationContentHeight: number;
  horizontalRegions: HorizontalAnnotationRegion[];
  annotationToolbarAvailable: boolean;
  annotationTool: string;
}

interface MaterialReaderHost {
  data: MaterialReaderData;
  setData(patch: Partial<MaterialReaderData>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
  createSelectorQuery(): WechatMiniprogram.SelectorQuery;
}

type MaterialReaderViewData = Omit<
  MaterialReaderData,
  | "appearance"
  | "blocks"
  | "inlineHeaderAvailable"
  | "anchors"
  | "state"
  | "title"
  | "subtitle"
  | "chipLabel"
  | "returnLabel"
  | "annotateAvailable"
  | "annotateActive"
  | "interactionActive"
  | "inlineAnnotateButton"
  | "annotationOpen"
  | "annotationStrokes"
  | "annotationElapsedSeconds"
  | "annotationElapsedMs"
  | "annotationToolbarAvailable"
  | "annotationTool"
>;

const initialReaderData: MaterialReaderViewData = {
  readerMode: "question_first",
  position: { anchorId: "material-start", scrollTop: 0, zoom: 1 },
  preferences: { fontScale: 1, colorMode: "standard", lineSpacing: 1 },
  scrollIntoView: "",
  zoomPercent: 100,
  fontPercent: 100,
  eyeCare: false,
  statusBarHeight: 0,
  capsuleReserve: 0,
  readPercent: 0,
  annotationContentHeight: 0,
  horizontalRegions: [],
};

Component({
  properties: {
    appearance: { type: String, value: "standard" },
    inlineHeaderAvailable: { type: Boolean, value: true },
    title: { type: String, value: "材料" },
    /** Figma 81:8：导航行副标题，如「资料分析 · 第 1-5 题」。 */
    subtitle: { type: String, value: "" },
    /** Figma 81:12：正文卡顶部的题型胶囊。 */
    chipLabel: { type: String, value: "" },
    /** Figma 88:2：「回到第 N 题」。 */
    returnLabel: { type: String, value: "" },
    annotateAvailable: { type: Boolean, value: false },
    annotateActive: { type: Boolean, value: false },
    /** 宿主 interaction projection 关闭时，物理卸载全屏触摸捕获面。 */
    interactionActive: { type: Boolean, value: true },
    inlineAnnotateButton: { type: Boolean, value: false },
    annotationOpen: { type: Boolean, value: false },
    annotationStrokes: { type: null, value: [] },
    annotationElapsedSeconds: { type: Number, value: 0 },
    annotationElapsedMs: { type: Number, value: -1 },
    annotationToolbarAvailable: { type: Boolean, value: true },
    annotationTool: { type: String, value: "pan" },
    blocks: { type: null, value: [] },
    anchors: { type: null, value: [] },
    state: { type: Object, value: createMaterialReaderState() },
  },
  data: initialReaderData,
  observers: {
    state() {
      syncReader(this);
    },
    readerMode(mode: string) {
      if (mode === "fullscreen") this.measureInitialReadPercent();
      this.measureAnnotationContent();
    },
    blocks() {
      this.measureAnnotationContent();
    },
  },
  lifetimes: {
    attached() {
      this.setData(measureSystemChrome(8));
      syncReader(this);
      this.measureAnnotationContent();
    },
  },
  methods: {
    openDrawer() {
      transition(this, { type: "OPEN_DRAWER" });
    },
    openFullscreen() {
      transition(this, { type: "OPEN_FULLSCREEN" });
    },
    returnToQuestion() {
      transition(this, { type: "RETURN_TO_QUESTION" });
    },
    onScroll(event: WechatMiniprogram.ScrollViewScroll) {
      transition(this, {
        type: "SCROLLED",
        scrollTop: event.detail.scrollTop,
      });
      this.measureReadPercent(event.detail.scrollTop, event.detail.scrollHeight);
      if (
        Number.isFinite(event.detail.scrollHeight) &&
        event.detail.scrollHeight > 0 &&
        Math.abs(event.detail.scrollHeight - this.data.annotationContentHeight) >= 1
      ) {
        this.setData({ annotationContentHeight: event.detail.scrollHeight });
      }
    },
    measureAnnotationContent() {
      wx.nextTick(() => {
        const query = this.createSelectorQuery();
        query
          .select(
            this.data.readerMode === "question_first"
              ? ".material-reader__inline-scroll"
              : ".material-reader__body-scroll",
          )
          .fields({ size: true, scrollOffset: true });
        query.exec((results: Array<{ height?: number; scrollHeight?: number } | null>) => {
          const measured = results[0];
          const scrollHeight = measured?.scrollHeight ?? 0;
          const viewportHeight = measured?.height ?? 0;
          // scroll-view 才拥有可靠的 scrollHeight。短材料在部分基础库中会回传
          // scrollHeight: 0，此时至少使用可触摸的视口高度，不能把画布留成 0 高。
          const height = scrollHeight > 0 ? scrollHeight : viewportHeight;
          if (height > 0 && Math.abs(height - this.data.annotationContentHeight) >= 1) {
            this.setData({ annotationContentHeight: height });
          }
        });
      });
    },
    /** 打开沉浸阅读时先量一次，短材料立即显示真实的 100%。 */
    measureInitialReadPercent() {
      const query = this.createSelectorQuery();
      query.select(".material-reader__body-scroll").boundingClientRect();
      query.select(".material-reader__body").boundingClientRect();
      query.exec((results: Array<{ height?: number } | null>) => {
        const viewport = results[0]?.height ?? 0;
        const content = results[1]?.height ?? 0;
        if (viewport <= 0 || content <= 0) return;
        this.applyReadPercent(this.data.position.scrollTop, content, viewport);
      });
    },
    /** 已读比例来自真实滚动测量（Figma 81:36-38）。 */
    measureReadPercent(scrollTop: number, scrollHeight: number) {
      const query = this.createSelectorQuery();
      query.select(".material-reader__body-scroll").boundingClientRect();
      query.exec((results: Array<{ height?: number } | null>) => {
        const viewport = results[0]?.height ?? 0;
        if (!Number.isFinite(scrollHeight) || scrollHeight <= 0 || viewport <= 0) return;
        this.applyReadPercent(scrollTop, scrollHeight, viewport);
      });
    },
    applyReadPercent(scrollTop: number, contentHeight: number, viewport: number) {
      const percent =
        contentHeight <= viewport
          ? 100
          : Math.min(100, Math.max(0, Math.round(((scrollTop + viewport) / contentHeight) * 100)));
      if (percent !== this.data.readPercent) this.setData({ readPercent: percent });
    },
    onAnchorChange(event: WechatMiniprogram.CustomEvent<{ anchorId: string }>) {
      transition(this, {
        type: "ANCHOR_CHANGED",
        anchorId: event.detail.anchorId,
      });
    },
    onZoomChange(event: WechatMiniprogram.CustomEvent<{ zoom: number }>) {
      transition(this, {
        type: "ZOOM_CHANGED",
        zoom: event.detail.zoom,
      });
    },
    decreaseZoom() {
      transition(this, {
        type: "ZOOM_CHANGED",
        zoom: this.data.position.zoom - 0.25,
      });
    },
    increaseZoom() {
      transition(this, {
        type: "ZOOM_CHANGED",
        zoom: this.data.position.zoom + 0.25,
      });
    },
    decreaseFont() {
      transition(this, {
        type: "FONT_SCALE_CHANGED",
        fontScale: nextFontScale(this.data.preferences.fontScale, "decrease"),
      });
    },
    increaseFont() {
      transition(this, {
        type: "FONT_SCALE_CHANGED",
        fontScale: nextFontScale(this.data.preferences.fontScale, "increase"),
      });
    },
    /** Figma 81:40：单键循环字号。 */
    cycleFont() {
      transition(this, {
        type: "FONT_SCALE_CHANGED",
        fontScale: cycleFontScale(this.data.preferences.fontScale),
      });
    },
    /** Figma 81:43：单键循环行距。 */
    cycleLineSpacing() {
      transition(this, {
        type: "LINE_SPACING_CHANGED",
        lineSpacing: nextContentLineSpacing(this.data.preferences.lineSpacing),
      });
    },
    toggleEyeCare() {
      transition(this, {
        type: "COLOR_MODE_CHANGED",
        colorMode: this.data.preferences.colorMode === "eye_care" ? "standard" : "eye_care",
      });
    },
    /** Figma 81:52：批注入口交给宿主页面打开题面批注层。 */
    requestAnnotate() {
      this.triggerEvent("annotate");
    },
    onAnnotationStrokesChange(
      event: WechatMiniprogram.CustomEvent<{ strokes: unknown; action: "added" | "erased" }>,
    ) {
      this.triggerEvent("strokeschange", event.detail);
    },
    onHorizontalRegions(
      event: WechatMiniprogram.CustomEvent<{
        regions: Array<Omit<HorizontalAnnotationRegion, "measuredScrollTop">>;
      }>,
    ) {
      this.setData({
        horizontalRegions: event.detail.regions.map((region) => ({
          ...region,
          measuredScrollTop: this.data.position.scrollTop,
        })),
      });
    },
    finishAnnotation() {
      this.triggerEvent("annotatedone");
    },
    openAnnotationMore() {
      this.triggerEvent("annotationmore");
    },
    changeAnnotationTool(event: WechatMiniprogram.CustomEvent<{ tool: string }>) {
      this.triggerEvent("annotationtoolchange", event.detail);
    },
    focusAnnotationCanvas() {
      this.triggerEvent("annotationfocus");
    },
    stopPropagation() {},
  },
});

function syncReader(host: MaterialReaderHost): void {
  applyState(host, restoreMaterialReaderState(host.data.state), "");
}

function transition(host: MaterialReaderHost, event: MaterialReaderEvent): void {
  const next = reduceMaterialReader(currentState(host), event);
  const scrollIntoView =
    event.type === "ANCHOR_CHANGED" ? contentAnchorDomId(next.position.anchorId) : "";
  applyState(host, next, scrollIntoView);
  host.triggerEvent("statechange", { state: next });
  if (
    event.type === "POSITION_CHANGED" ||
    event.type === "ANCHOR_CHANGED" ||
    event.type === "SCROLLED" ||
    event.type === "ZOOM_CHANGED"
  ) {
    host.triggerEvent("positionchange", { position: next.position });
  }
  if (
    event.type === "OPEN_DRAWER" ||
    event.type === "OPEN_FULLSCREEN" ||
    event.type === "RETURN_TO_QUESTION"
  ) {
    host.triggerEvent("modechange", { mode: next.mode });
  }
  if (
    event.type === "FONT_SCALE_CHANGED" ||
    event.type === "LINE_SPACING_CHANGED" ||
    event.type === "COLOR_MODE_CHANGED"
  ) {
    host.triggerEvent("preferenceschange", { preferences: next.preferences });
  }
}

function currentState(host: MaterialReaderHost): MaterialReaderState {
  return {
    mode: host.data.readerMode,
    position: host.data.position,
    preferences: host.data.preferences,
  };
}

function applyState(
  host: MaterialReaderHost,
  state: MaterialReaderState,
  scrollIntoView: string,
): void {
  host.setData({
    readerMode: state.mode,
    position: state.position,
    preferences: state.preferences,
    scrollIntoView,
    zoomPercent: Math.round(state.position.zoom * 100),
    fontPercent: Math.round(state.preferences.fontScale * 100),
    eyeCare: state.preferences.colorMode === "eye_care",
  });
}
