import {
  collectPreviewUrls,
  defaultContentPreferences,
  normalizeContentPreferences,
  normalizeContentZoom,
  prepareRichContent,
  type ContentAnchor,
  type ContentDisplayPreferences,
  type RichContentBlock,
  type RichContentView,
} from "./model";

interface ContentRendererData {
  blocks: RichContentBlock[];
  anchors: ContentAnchor[];
  zoom: number;
  preferences: ContentDisplayPreferences;
  freezeFirstColumn: boolean;
  freezeHeader: boolean;
  viewBlocks: RichContentView[];
  previewUrls: string[];
  fontScale: number;
  colorMode: "standard" | "eye_care";
}

interface ContentRendererHost {
  data: ContentRendererData;
  setData(patch: Partial<ContentRendererData>): void;
  triggerEvent(name: string, detail?: Record<string, unknown>): void;
}

Component({
  options: {
    virtualHost: true,
  },
  properties: {
    blocks: { type: Array, value: [] },
    anchors: { type: Array, value: [] },
    zoom: { type: Number, value: 1 },
    preferences: { type: Object, value: defaultContentPreferences },
    freezeFirstColumn: { type: Boolean, value: true },
    freezeHeader: { type: Boolean, value: true },
  },
  data: {
    viewBlocks: [] as RichContentView[],
    previewUrls: [] as string[],
    fontScale: 1,
    colorMode: "standard" as const,
  },
  observers: {
    "blocks, anchors, zoom, preferences"() {
      syncContent(this);
    },
  },
  lifetimes: {
    attached() {
      syncContent(this);
    },
  },
  methods: {
    previewAsset(event: WechatMiniprogram.BaseEvent) {
      const current = String(event.currentTarget.dataset.current ?? "");
      if (!current) return;
      const urls = this.data.previewUrls.includes(current)
        ? this.data.previewUrls
        : [current, ...this.data.previewUrls];
      void wx.previewImage({ current, urls });
    },
    previewOcrOriginal(event: WechatMiniprogram.BaseEvent) {
      const current = String(event.currentTarget.dataset.current ?? "");
      const blockId = String(event.currentTarget.dataset.blockId ?? "");
      if (!current) return;
      this.triggerEvent("ocrsource", { blockId, url: current });
      void wx.previewImage({ current, urls: [current] });
    },
    activateAnchor(event: WechatMiniprogram.BaseEvent) {
      const anchorId = String(event.currentTarget.dataset.anchorId ?? "");
      if (anchorId) this.triggerEvent("anchorchange", { anchorId });
    },
    decreaseZoom(event: WechatMiniprogram.BaseEvent) {
      emitZoom(this, event, -0.25);
    },
    increaseZoom(event: WechatMiniprogram.BaseEvent) {
      emitZoom(this, event, 0.25);
    },
    noop() {},
  },
});

function syncContent(host: ContentRendererHost): void {
  const blocks = host.data.blocks;
  const zoom = normalizeContentZoom(host.data.zoom);
  const preferences = normalizeContentPreferences(host.data.preferences);
  host.setData({
    viewBlocks: prepareRichContent(blocks, zoom, host.data.anchors),
    previewUrls: collectPreviewUrls(blocks),
    fontScale: preferences.fontScale,
    colorMode: preferences.colorMode,
  });
}

function emitZoom(
  host: ContentRendererHost,
  event: WechatMiniprogram.BaseEvent,
  delta: number,
): void {
  const blockId = String(event.currentTarget.dataset.blockId ?? "");
  const zoom = normalizeContentZoom(host.data.zoom + delta);
  host.triggerEvent("zoomchange", { blockId, zoom, source: "table" });
}
