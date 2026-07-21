import { contentAnchorDomId, type ContentDisplayPreferences } from "../content-renderer/model";
import {
  createMaterialReaderState,
  nextFontScale,
  reduceMaterialReader,
  restoreMaterialReaderState,
  type MaterialReaderEvent,
  type MaterialReaderMode,
  type MaterialReaderPosition,
  type MaterialReaderState,
} from "./state";

interface MaterialReaderData {
  blocks: unknown;
  anchors: unknown;
  state: MaterialReaderState;
  title: string;
  readerMode: MaterialReaderMode;
  position: MaterialReaderPosition;
  preferences: ContentDisplayPreferences;
  scrollIntoView: string;
  zoomPercent: number;
  fontPercent: number;
  eyeCare: boolean;
}

interface MaterialReaderHost {
  data: MaterialReaderData;
  setData(patch: Partial<MaterialReaderData>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
}

type MaterialReaderViewData = Omit<MaterialReaderData, "blocks" | "anchors" | "state" | "title">;

const initialReaderData: MaterialReaderViewData = {
  readerMode: "question_first",
  position: { anchorId: "material-start", scrollTop: 0, zoom: 1 },
  preferences: { fontScale: 1, colorMode: "standard" },
  scrollIntoView: "",
  zoomPercent: 100,
  fontPercent: 100,
  eyeCare: false,
};

Component({
  properties: {
    title: { type: String, value: "材料" },
    blocks: { type: null, value: [] },
    anchors: { type: null, value: [] },
    state: { type: Object, value: createMaterialReaderState() },
  },
  data: initialReaderData,
  observers: {
    state() {
      syncReader(this);
    },
  },
  lifetimes: {
    attached() {
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
    toggleEyeCare() {
      transition(this, {
        type: "COLOR_MODE_CHANGED",
        colorMode: this.data.preferences.colorMode === "eye_care" ? "standard" : "eye_care",
      });
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
  if (event.type === "FONT_SCALE_CHANGED" || event.type === "COLOR_MODE_CHANGED") {
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
