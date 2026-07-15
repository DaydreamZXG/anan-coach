import {
  defaultContentPreferences,
  normalizeContentPreferences,
  normalizeContentZoom,
  type ContentDisplayPreferences,
} from "../content-renderer/model";

export type MaterialReaderMode = "question_first" | "drawer" | "fullscreen";

export interface MaterialReaderPosition {
  anchorId: string;
  scrollTop: number;
  zoom: number;
}

export interface MaterialReaderState {
  mode: MaterialReaderMode;
  position: MaterialReaderPosition;
  preferences: ContentDisplayPreferences;
}

export type MaterialReaderEvent =
  | { type: "OPEN_DRAWER" }
  | { type: "OPEN_FULLSCREEN" }
  | { type: "RETURN_TO_QUESTION" }
  | { type: "POSITION_CHANGED"; position: MaterialReaderPosition }
  | { type: "ANCHOR_CHANGED"; anchorId: string }
  | { type: "SCROLLED"; scrollTop: number }
  | { type: "ZOOM_CHANGED"; zoom: number }
  | { type: "FONT_SCALE_CHANGED"; fontScale: number }
  | { type: "COLOR_MODE_CHANGED"; colorMode: ContentDisplayPreferences["colorMode"] };

export class MaterialReaderStateError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "MaterialReaderStateError";
  }
}

export function createMaterialReaderState(
  position?: MaterialReaderPosition,
  preferences: ContentDisplayPreferences = defaultContentPreferences,
): MaterialReaderState {
  return {
    mode: "question_first",
    position: normalizePosition(position ?? { anchorId: "material-start", scrollTop: 0, zoom: 1 }),
    preferences: normalizeContentPreferences(preferences),
  };
}

export function restoreMaterialReaderState(state: MaterialReaderState): MaterialReaderState {
  assertMode(state.mode);
  return {
    mode: state.mode,
    position: normalizePosition(state.position),
    preferences: normalizeContentPreferences(state.preferences),
  };
}

export function reduceMaterialReader(
  state: MaterialReaderState,
  event: MaterialReaderEvent,
): MaterialReaderState {
  const current = restoreMaterialReaderState(state);
  if (event.type === "OPEN_DRAWER") return { ...current, mode: "drawer" };
  if (event.type === "OPEN_FULLSCREEN") return { ...current, mode: "fullscreen" };
  if (event.type === "RETURN_TO_QUESTION") return { ...current, mode: "question_first" };
  if (event.type === "POSITION_CHANGED") {
    return { ...current, position: normalizePosition(event.position) };
  }
  if (event.type === "ANCHOR_CHANGED") {
    return {
      ...current,
      position: normalizePosition({ ...current.position, anchorId: event.anchorId }),
    };
  }
  if (event.type === "SCROLLED") {
    return {
      ...current,
      position: normalizePosition({ ...current.position, scrollTop: event.scrollTop }),
    };
  }
  if (event.type === "ZOOM_CHANGED") {
    return {
      ...current,
      position: normalizePosition({ ...current.position, zoom: event.zoom }),
    };
  }
  if (event.type === "FONT_SCALE_CHANGED") {
    return {
      ...current,
      preferences: normalizeContentPreferences({
        ...current.preferences,
        fontScale: event.fontScale,
      }),
    };
  }
  return {
    ...current,
    preferences: normalizeContentPreferences({
      ...current.preferences,
      colorMode: event.colorMode,
    }),
  };
}

export function nextFontScale(current: number, direction: "decrease" | "increase"): number {
  const next = Math.round((current + (direction === "increase" ? 0.1 : -0.1)) * 10) / 10;
  return Math.min(1.3, Math.max(0.9, next));
}

function normalizePosition(position: MaterialReaderPosition): MaterialReaderPosition {
  const anchorId = position.anchorId.trim();
  if (!anchorId) throw new MaterialReaderStateError("材料位置必须包含锚点。");
  if (anchorId.length > 240) throw new MaterialReaderStateError("材料锚点长度超过 240。");
  if (!Number.isFinite(position.scrollTop) || position.scrollTop < 0) {
    throw new MaterialReaderStateError("材料滚动位置必须是非负有限数字。");
  }
  return {
    anchorId,
    scrollTop: Math.round(position.scrollTop),
    zoom: normalizeContentZoom(position.zoom),
  };
}

function assertMode(mode: string): void {
  if (mode !== "question_first" && mode !== "drawer" && mode !== "fullscreen") {
    throw new MaterialReaderStateError("材料阅读器模式无效。");
  }
}
