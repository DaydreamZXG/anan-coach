import {
  contentAnchorDomId,
  nextContentLineSpacing,
  type ContentDisplayPreferences,
} from "../content-renderer/model";
import { measureSystemChrome } from "../../../../../components/safe-top/metrics";
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
    },
  },
  lifetimes: {
    attached() {
      this.setData(measureSystemChrome(8));
      syncReader(this);
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
