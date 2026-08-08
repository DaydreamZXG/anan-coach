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
  blocks: unknown;
  anchors: unknown;
  zoom: number;
  preferences: ContentDisplayPreferences;
  freezeFirstColumn: boolean;
  freezeHeader: boolean;
  viewBlocks: RichContentView[];
  previewUrls: string[];
  fontScale: number;
  lineSpacing: number;
  colorMode: "standard" | "eye_care";
  selectable: boolean;
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
    blocks: { type: null, value: [] },
    anchors: { type: null, value: [] },
    zoom: { type: Number, value: 1 },
    preferences: { type: Object, value: defaultContentPreferences },
    freezeFirstColumn: { type: Boolean, value: true },
    freezeHeader: { type: Boolean, value: true },
    /** Figma 81:16：沉浸阅读为段落加浅灰序号。 */
    numbered: { type: Boolean, value: false },
    /** Figma 84:2 画词：题干按句段可选中。 */
    selectable: { type: Boolean, value: false },
    selectedSegmentKey: { type: String, value: "" },
  },
  data: {
    viewBlocks: [] as RichContentView[],
    previewUrls: [] as string[],
    fontScale: 1,
    lineSpacing: 1,
    colorMode: "standard" as const,
    selectable: false,
  },
  observers: {
    "blocks, anchors, zoom, preferences"() {
      syncContent(this);
    },
    selectable() {
      this.measureSegmentRects();
    },
  },
  lifetimes: {
    attached() {
      syncContent(this);
    },
  },
  methods: {
    /** 表格横滚使用完整内容宽度存 x；把视口/scrollLeft 测量交给批注层换算。 */
    measureHorizontalRegions() {
      const query = this.createSelectorQuery();
      query
        .selectAll(".content-table__scroll")
        .fields({ dataset: true, rect: true, size: true, scrollOffset: true });
      query.selectAll(".content-table__grid").fields({ size: true });
      query.exec(
        (
          results: Array<
            Array<{
              dataset?: Record<string, unknown>;
              left?: number;
              top?: number;
              width?: number;
              height?: number;
              scrollLeft?: number;
            } | null>
          >,
        ) => {
          const scrolls = results[0] ?? [];
          const grids = results[1] ?? [];
          const regions = scrolls.flatMap((scroll, index) => {
            const rawBlockId = scroll?.dataset?.blockId;
            const rawLabel = scroll?.dataset?.label;
            const blockId = typeof rawBlockId === "string" ? rawBlockId : "";
            const viewportWidth = scroll?.width ?? 0;
            const contentWidth = grids[index]?.width ?? viewportWidth;
            if (!blockId || viewportWidth <= 0 || contentWidth <= 0) return [];
            return [
              {
                blockId,
                label: typeof rawLabel === "string" ? rawLabel : "资料表格",
                viewportLeft: scroll?.left ?? 0,
                viewportTop: scroll?.top ?? 0,
                viewportWidth,
                viewportHeight: scroll?.height ?? 0,
                contentWidth,
                scrollLeft: scroll?.scrollLeft ?? 0,
              },
            ];
          });
          this.triggerEvent("horizontalregions", { regions });
        },
      );
    },
    onTableScroll() {
      this.measureHorizontalRegions();
    },
    /** 契约增量 v2：把题干可选中句段的视口矩形上报给宿主（批注锚定用）。 */
    measureSegmentRects() {
      if (!this.data.selectable) return;
      const query = this.createSelectorQuery();
      query
        .selectAll(".content-segment")
        .fields({ dataset: true, rect: true })
        .exec(
          (
            results: Array<
              Array<{
                dataset?: Record<string, unknown>;
                left?: number;
                top?: number;
                width?: number;
                height?: number;
              } | null>
            > | null,
          ) => {
            const items = results?.[0];
            if (!items) return;
            const rects = items
              .map((item) => {
                const ds = item?.dataset;
                if (!ds) return null;
                const blockId = typeof ds.blockId === "string" ? ds.blockId : "";
                const segmentIndex =
                  typeof ds.segmentIndex === "string"
                    ? Number(ds.segmentIndex)
                    : Number(ds.segmentIndex);
                const segmentText = typeof ds.text === "string" ? ds.text : "";
                if (
                  !blockId ||
                  !Number.isInteger(segmentIndex) ||
                  segmentIndex < 0 ||
                  !segmentText
                ) {
                  return null;
                }
                return {
                  blockId,
                  segmentIndex,
                  segmentText,
                  left: item.left ?? 0,
                  top: item.top ?? 0,
                  width: item.width ?? 0,
                  height: item.height ?? 0,
                };
              })
              .filter((entry): entry is NonNullable<typeof entry> => entry !== null);
            this.triggerEvent("segmentrects", { segments: rects });
          },
        );
    },
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
      // 只有真实材料锚点（anchorId ≠ 退化成的 blockId）才触发跳转；
      // 普通段落点一下不应让材料滚动。
      if (!anchorId || anchorId === String(event.currentTarget.dataset.blockId ?? "")) return;
      this.triggerEvent("anchorchange", { anchorId });
    },
    /** Figma 84:2 画词：点选句段，连同视口内矩形一起交给宿主页面定位工具条。 */
    tapSegment(event: WechatMiniprogram.BaseEvent) {
      const blockId = String(event.currentTarget.dataset.blockId ?? "");
      const segmentIndex = Number(event.currentTarget.dataset.segmentIndex ?? -1);
      const text = String(event.currentTarget.dataset.text ?? "");
      if (!blockId || segmentIndex < 0 || !text.trim()) return;
      const key = `${blockId}:${String(segmentIndex)}`;
      const query = this.createSelectorQuery();
      query.select(`#segment-${blockId}-${String(segmentIndex)}`).boundingClientRect();
      query.exec(
        (
          results: Array<{
            top?: number;
            bottom?: number;
            left?: number;
            right?: number;
          } | null>,
        ) => {
          this.triggerEvent("segmenttap", {
            key,
            text,
            rect: results[0] ?? null,
          });
        },
      );
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
  const blocks = normalizeBlocks(host.data.blocks);
  const anchors = normalizeAnchors(host.data.anchors);
  const zoom = normalizeContentZoom(host.data.zoom);
  const preferences = normalizeContentPreferences(host.data.preferences);
  host.setData({
    viewBlocks: prepareRichContent(blocks, zoom, anchors),
    previewUrls: collectPreviewUrls(blocks),
    fontScale: preferences.fontScale,
    lineSpacing: preferences.lineSpacing ?? 1,
    colorMode: preferences.colorMode,
  });
  // 契约增量 v2：渲染完成后立即测量句段矩形并上报（virtualHost 内测量，宿主选择器够不到）。
  // setData 异步更新视图，延到下一拍再量，保证拿到本次渲染后的 DOM。
  if (host.data.selectable) {
    wx.nextTick(() => {
      (host as unknown as { measureSegmentRects?: () => void }).measureSegmentRects?.();
    });
  }
  wx.nextTick(() => {
    (host as unknown as { measureHorizontalRegions?: () => void }).measureHorizontalRegions?.();
  });
}

function normalizeBlocks(value: unknown): RichContentBlock[] {
  return Array.isArray(value) ? (value as RichContentBlock[]) : [];
}

function normalizeAnchors(value: unknown): ContentAnchor[] {
  return Array.isArray(value) ? (value as ContentAnchor[]) : [];
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
