export type ContentColorMode = "standard" | "eye_care";

/** Figma 81:43「行距」档位：正文 line-height 的缩放倍数。 */
export const contentLineSpacings = [1, 1.15, 1.3] as const;
export type ContentLineSpacing = (typeof contentLineSpacings)[number];

export interface ContentDisplayPreferences {
  fontScale: number;
  colorMode: ContentColorMode;
  /** 缺省 1，旧存档没有该字段时视为标准行距。 */
  lineSpacing?: ContentLineSpacing;
}

export interface ContentAssetMetadata {
  width?: number;
  height?: number;
}

interface ContentBlockBase {
  blockId: string;
}

export interface ParagraphContentBlock extends ContentBlockBase {
  type: "paragraph";
  text: string;
  html?: string | null;
}

export interface FormulaContentBlock extends ContentBlockBase {
  type: "formula";
  expression: string;
  format: "latex";
  display: boolean;
  renderAsset?: {
    url: string;
    width?: number;
    height?: number;
  };
}

interface VisualContentBlock extends ContentBlockBase, ContentAssetMetadata {
  assetId: string;
  url: string;
  originalOcrUrl: string;
  altText: string;
  zoomable: true;
  highResolutionUrl?: string;
  highResolutionWidth?: number;
  highResolutionHeight?: number;
  caption?: string;
}

export interface ImageContentBlock extends VisualContentBlock {
  type: "image";
}

export interface GraphContentBlock extends VisualContentBlock {
  type: "graph";
  sourceLabel?: string;
}

export interface TableContentCell {
  row: number;
  column: number;
  rowSpan: number;
  columnSpan: number;
  header: boolean;
  text: string;
  html?: string | null;
  align?: "left" | "center" | "right";
}

export interface TableContentBlock extends ContentBlockBase {
  type: "table";
  rowCount: number;
  columnCount: number;
  frozenHeaderRows: number;
  horizontallyScrollable: true;
  cells: TableContentCell[];
  caption?: string;
  columnWidths?: number[];
  rowHeights?: number[];
}

export type RichContentBlock =
  | ParagraphContentBlock
  | FormulaContentBlock
  | ImageContentBlock
  | GraphContentBlock
  | TableContentBlock;

export interface ContentAnchor {
  anchorId: string;
  blockId: string;
  label: string;
}

interface ContentViewBase {
  id: string;
  blockId: string;
  anchorId: string;
  domAnchorId: string;
}

export interface ParagraphContentView extends ParagraphContentBlock, ContentViewBase {
  kind: "paragraph";
  hasHtml: boolean;
  /** Figma 84:2 画词：按标点切分的可选中片段（拼接后等于原文）。 */
  segments: string[];
}

/** 按中文标点切分文本，保留分隔符，供画词选择使用。 */
export function segmentSelectableText(text: string): string[] {
  const segments: string[] = [];
  let current = "";
  for (const character of text) {
    current += character;
    if ("。；？！，、：".includes(character)) {
      segments.push(current);
      current = "";
    }
  }
  if (current) segments.push(current);
  return segments.length > 0 ? segments : [text];
}

export interface FormulaContentView extends FormulaContentBlock, ContentViewBase {
  kind: "formula";
  displayMode: "inline" | "block";
  hasRenderAsset: boolean;
  renderWidth: number;
}

export interface AssetContentView extends ImageContentBlock, ContentViewBase {
  kind: "image";
  originalUrl: string;
  hasOcrOriginal: boolean;
}

export interface GraphContentView extends GraphContentBlock, ContentViewBase {
  kind: "graph";
  originalUrl: string;
  hasOcrOriginal: boolean;
}

export interface TableCellView extends TableContentCell {
  id: string;
  gridStyle: string;
  frozenColumn: boolean;
  frozenHeader: boolean;
  hasHtml: boolean;
}

export interface TableContentView extends TableContentBlock, ContentViewBase {
  kind: "table";
  resolvedCaption: string;
  gridStyle: string;
  tableWidth: number;
  tableHeight: number;
  cells: TableCellView[];
}

export type RichContentView =
  | ParagraphContentView
  | FormulaContentView
  | AssetContentView
  | GraphContentView
  | TableContentView;

export class ContentContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ContentContractError";
  }
}

export const defaultContentPreferences: ContentDisplayPreferences = {
  fontScale: 1,
  colorMode: "standard",
};

const minimumZoom = 0.75;
const maximumZoom = 2;

export function normalizeContentZoom(value: number): number {
  if (!Number.isFinite(value)) throw new ContentContractError("内容缩放比例必须是有限数字。");
  return Math.min(maximumZoom, Math.max(minimumZoom, Math.round(value * 100) / 100));
}

export function normalizeContentPreferences(preferences: {
  fontScale: number;
  colorMode: string;
  lineSpacing?: number;
}): ContentDisplayPreferences {
  if (!Number.isFinite(preferences.fontScale)) {
    throw new ContentContractError("内容字号比例必须是有限数字。");
  }
  if (preferences.fontScale < 0.9 || preferences.fontScale > 1.3) {
    throw new ContentContractError("内容字号比例必须在 0.9 到 1.3 之间。");
  }
  const colorMode = preferences.colorMode;
  if (colorMode !== "standard" && colorMode !== "eye_care") {
    throw new ContentContractError("内容显示模式不受支持。");
  }
  const lineSpacing = preferences.lineSpacing ?? 1;
  if (!contentLineSpacings.includes(lineSpacing as ContentLineSpacing)) {
    throw new ContentContractError("内容行距档位不受支持。");
  }
  return {
    fontScale: Math.round(preferences.fontScale * 10) / 10,
    colorMode,
    lineSpacing: lineSpacing as ContentLineSpacing,
  };
}

/** 行距在 [1, 1.15, 1.3] 之间循环，供沉浸阅读的「行距」按钮使用。 */
export function nextContentLineSpacing(current: number | undefined): ContentLineSpacing {
  const index = contentLineSpacings.indexOf((current ?? 1) as ContentLineSpacing);
  return contentLineSpacings[(index + 1) % contentLineSpacings.length] as ContentLineSpacing;
}

export function prepareRichContent(
  blocks: readonly RichContentBlock[],
  zoom: number,
  anchors: readonly ContentAnchor[] = [],
): RichContentView[] {
  const normalizedZoom = normalizeContentZoom(zoom);
  const anchorByBlockId = validateContentIdentity(blocks, anchors);
  return blocks.map((block) =>
    prepareBlock(
      block,
      normalizedZoom,
      anchorByBlockId.get(block.blockId)?.anchorId ?? block.blockId,
    ),
  );
}

export function collectPreviewUrls(blocks: readonly RichContentBlock[]): string[] {
  const urls: string[] = [];
  for (const block of blocks) {
    if (block.type !== "image" && block.type !== "graph") continue;
    const url = block.highResolutionUrl ?? block.url;
    assertSafeAssetUrl(url, `${block.blockId} 高清图`);
    if (!urls.includes(url)) urls.push(url);
  }
  return urls;
}

export function contentAnchorDomId(anchorId: string): string {
  const normalized = anchorId
    .normalize("NFKC")
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 96);
  const suffix = stableHash(anchorId);
  return `material-anchor-${normalized || "anchor"}-${suffix}`;
}

function prepareBlock(block: RichContentBlock, zoom: number, anchorId: string): RichContentView {
  assertBlockId(block.blockId);
  const identity: ContentViewBase = {
    id: block.blockId,
    blockId: block.blockId,
    anchorId,
    domAnchorId: contentAnchorDomId(anchorId),
  };
  if (block.type === "paragraph") {
    assertText(block.text, `${block.blockId} 正文`);
    assertSafeHtml(block.html, `${block.blockId} 正文 HTML`);
    return {
      ...block,
      ...identity,
      kind: "paragraph",
      hasHtml: Boolean(block.html),
      segments: segmentSelectableText(block.text),
    };
  }
  if (block.type === "formula") {
    assertText(block.expression, `${block.blockId} 公式表达式`);
    if (block.renderAsset) assertOptionalRenderAsset(block.renderAsset, block.blockId);
    return {
      ...block,
      ...identity,
      kind: "formula",
      displayMode: block.display ? "block" : "inline",
      hasRenderAsset: Boolean(block.renderAsset),
      renderWidth: Math.round((block.renderAsset?.width ?? 0) * zoom),
    };
  }
  if (block.type === "image") {
    assertVisual(block);
    return {
      ...block,
      ...identity,
      kind: "image",
      originalUrl: block.highResolutionUrl ?? block.url,
      hasOcrOriginal: Boolean(block.originalOcrUrl),
    };
  }
  if (block.type === "graph") {
    assertVisual(block);
    return {
      ...block,
      ...identity,
      kind: "graph",
      originalUrl: block.highResolutionUrl ?? block.url,
      hasOcrOriginal: Boolean(block.originalOcrUrl),
    };
  }
  return { ...prepareTable(block, zoom), ...identity };
}

function prepareTable(
  block: TableContentBlock,
  zoom: number,
): Omit<TableContentView, keyof ContentViewBase> {
  if (!Number.isInteger(block.columnCount) || block.columnCount < 1 || block.columnCount > 100) {
    throw new ContentContractError(`${block.blockId} 表格列数必须在 1 到 100 之间。`);
  }
  if (!Number.isInteger(block.rowCount) || block.rowCount < 1 || block.rowCount > 1_000) {
    throw new ContentContractError(`${block.blockId} 表格行数必须在 1 到 1000 之间。`);
  }
  if (
    !Number.isInteger(block.frozenHeaderRows) ||
    block.frozenHeaderRows < 0 ||
    block.frozenHeaderRows > block.rowCount
  ) {
    throw new ContentContractError(`${block.blockId} 固定表头行数无效。`);
  }
  const columnWidths = block.columnWidths ?? defaultColumnWidths(block.columnCount);
  const rowHeights = block.rowHeights ?? defaultRowHeights(block.rowCount, block.frozenHeaderRows);
  assertDimensions(columnWidths, block.columnCount, `${block.blockId} 列宽`);
  assertDimensions(rowHeights, block.rowCount, `${block.blockId} 行高`);
  const occupied = Array.from({ length: block.rowCount }, () =>
    Array.from({ length: block.columnCount }, () => false),
  );
  const cellIds = new Set<string>();
  const scaledColumns = columnWidths.map((value) => Math.round(value * zoom));
  const scaledRows = rowHeights.map((value) => Math.round(value * zoom));
  const cells = block.cells.map((cell) => {
    const id = `${block.blockId}-cell-${String(cell.row)}-${String(cell.column)}`;
    if (cellIds.has(id)) throw new ContentContractError(`${block.blockId} 存在重复单元格 ${id}。`);
    cellIds.add(id);
    assertSafeHtml(cell.html, `${id} HTML`);
    assertCellRange(block, cell, id);
    for (let row = cell.row; row < cell.row + cell.rowSpan; row += 1) {
      for (let column = cell.column; column < cell.column + cell.columnSpan; column += 1) {
        if (occupied[row]?.[column]) {
          throw new ContentContractError(`${id} 与其他合并单元格重叠。`);
        }
        const occupiedRow = occupied[row];
        if (!occupiedRow) throw new ContentContractError(`${id} 行号越界。`);
        occupiedRow[column] = true;
      }
    }
    const stickyTop = scaledRows.slice(0, cell.row).reduce((total, value) => total + value, 0);
    const stickyLeft = scaledColumns
      .slice(0, cell.column)
      .reduce((total, value) => total + value, 0);
    const width = scaledColumns
      .slice(cell.column, cell.column + cell.columnSpan)
      .reduce((total, value) => total + value, 0);
    const height = scaledRows
      .slice(cell.row, cell.row + cell.rowSpan)
      .reduce((total, value) => total + value, 0);
    return {
      ...cell,
      id,
      align: cell.align ?? (cell.header ? "center" : "left"),
      frozenColumn: cell.column === 0,
      frozenHeader: cell.row < block.frozenHeaderRows,
      hasHtml: Boolean(cell.html),
      gridStyle: [
        `grid-column: ${String(cell.column + 1)} / span ${String(cell.columnSpan)}`,
        `grid-row: ${String(cell.row + 1)} / span ${String(cell.rowSpan)}`,
        `width: ${String(width)}rpx`,
        `height: ${String(height)}rpx`,
        `--content-sticky-top: ${String(stickyTop)}rpx`,
        `--content-sticky-left: ${String(stickyLeft)}rpx`,
        `text-align: ${cell.align ?? (cell.header ? "center" : "left")}`,
      ].join("; "),
    };
  });
  if (occupied.some((row) => row.some((value) => !value))) {
    throw new ContentContractError(`${block.blockId} 表格存在未定义的空单元格。`);
  }
  const tableWidth = scaledColumns.reduce((total, value) => total + value, 0);
  const tableHeight = scaledRows.reduce((total, value) => total + value, 0);
  return {
    ...block,
    kind: "table",
    resolvedCaption: block.caption ?? "数据表",
    columnWidths: [...columnWidths],
    rowHeights: [...rowHeights],
    cells,
    tableWidth,
    tableHeight,
    gridStyle: `grid-template-columns: ${scaledColumns.map((value) => `${String(value)}rpx`).join(" ")}; grid-template-rows: ${scaledRows.map((value) => `${String(value)}rpx`).join(" ")}; width: ${String(tableWidth)}rpx; height: ${String(tableHeight)}rpx`,
  };
}

function validateContentIdentity(
  blocks: readonly RichContentBlock[],
  anchors: readonly ContentAnchor[],
): Map<string, ContentAnchor> {
  const blockIds = new Set<string>();
  for (const block of blocks) {
    assertBlockId(block.blockId);
    if (blockIds.has(block.blockId)) {
      throw new ContentContractError(`存在重复内容块 ${block.blockId}。`);
    }
    blockIds.add(block.blockId);
  }
  const anchorIds = new Set<string>();
  const anchorByBlockId = new Map<string, ContentAnchor>();
  const domIds = new Set<string>();
  for (const anchor of anchors) {
    assertText(anchor.anchorId, "材料锚点标识");
    assertText(anchor.blockId, `${anchor.anchorId} 关联内容块`);
    assertText(anchor.label, `${anchor.anchorId} 锚点标题`);
    if (!blockIds.has(anchor.blockId)) {
      throw new ContentContractError(`${anchor.anchorId} 指向不存在的内容块。`);
    }
    if (anchorIds.has(anchor.anchorId)) {
      throw new ContentContractError(`存在重复材料锚点 ${anchor.anchorId}。`);
    }
    if (anchorByBlockId.has(anchor.blockId)) {
      throw new ContentContractError(`${anchor.blockId} 被多个材料锚点重复绑定。`);
    }
    const domId = contentAnchorDomId(anchor.anchorId);
    if (domIds.has(domId)) throw new ContentContractError("材料锚点 DOM 标识发生冲突。");
    anchorIds.add(anchor.anchorId);
    domIds.add(domId);
    anchorByBlockId.set(anchor.blockId, { ...anchor });
  }
  return anchorByBlockId;
}

function assertVisual(block: ImageContentBlock | GraphContentBlock): void {
  assertText(block.assetId, `${block.blockId} 资产标识`);
  assertText(block.altText, `${block.blockId} 图片替代文本`);
  assertSafeAssetUrl(block.url, `${block.blockId} 图片`);
  assertSafeAssetUrl(block.originalOcrUrl, `${block.blockId} OCR 原图`);
  if (block.highResolutionUrl) {
    assertSafeAssetUrl(block.highResolutionUrl, `${block.blockId} 高清图`);
  }
  assertOptionalDimension(block.width, `${block.blockId} 图片宽度`);
  assertOptionalDimension(block.height, `${block.blockId} 图片高度`);
  assertOptionalDimension(block.highResolutionWidth, `${block.blockId} 高清图宽度`);
  assertOptionalDimension(block.highResolutionHeight, `${block.blockId} 高清图高度`);
}

function assertOptionalRenderAsset(
  asset: NonNullable<FormulaContentBlock["renderAsset"]>,
  blockId: string,
): void {
  assertSafeAssetUrl(asset.url, `${blockId} 公式渲染资产`);
  assertOptionalDimension(asset.width, `${blockId} 公式宽度`);
  assertOptionalDimension(asset.height, `${blockId} 公式高度`);
}

function assertSafeAssetUrl(value: string, label: string): void {
  assertText(value, `${label}地址`);
  if (!value.startsWith("https://") && !value.startsWith("/")) {
    throw new ContentContractError(`${label}只允许 HTTPS 或小程序包内地址。`);
  }
}

function assertOptionalDimension(value: number | undefined, label: string): void {
  if (value === undefined) return;
  if (!Number.isInteger(value) || value < 1) {
    throw new ContentContractError(`${label}必须是正整数。`);
  }
}

function defaultColumnWidths(columnCount: number): number[] {
  return Array.from({ length: columnCount }, (_, index) => (index === 0 ? 196 : 176));
}

function defaultRowHeights(rowCount: number, frozenHeaderRows: number): number[] {
  return Array.from({ length: rowCount }, (_, index) => (index < frozenHeaderRows ? 88 : 78));
}

function assertDimensions(values: readonly number[], expected: number, label: string): void {
  if (values.length !== expected) throw new ContentContractError(`${label}数量与表格结构不一致。`);
  if (values.some((value) => !Number.isInteger(value) || value < 48 || value > 1200)) {
    throw new ContentContractError(`${label}必须是 48 到 1200 之间的整数。`);
  }
}

function assertCellRange(block: TableContentBlock, cell: TableContentCell, id: string): void {
  if (!Number.isInteger(cell.row) || !Number.isInteger(cell.column)) {
    throw new ContentContractError(`${id} 行列坐标必须是整数。`);
  }
  if (!Number.isInteger(cell.rowSpan) || !Number.isInteger(cell.columnSpan)) {
    throw new ContentContractError(`${id} 合并范围必须是整数。`);
  }
  if (cell.row < 0 || cell.column < 0 || cell.rowSpan < 1 || cell.columnSpan < 1) {
    throw new ContentContractError(`${id} 行列坐标或合并范围无效。`);
  }
  if (
    cell.row + cell.rowSpan > block.rowCount ||
    cell.column + cell.columnSpan > block.columnCount
  ) {
    throw new ContentContractError(`${id} 合并范围超出表格边界。`);
  }
}

function assertBlockId(value: string): void {
  assertText(value, "内容块标识");
  if (value.length > 240) throw new ContentContractError("内容块标识长度超过 240。");
}

function assertSafeHtml(value: string | null | undefined, label: string): void {
  if (!value) return;
  if (/<\s*(script|iframe|object|embed|link|meta)\b/i.test(value)) {
    throw new ContentContractError(`${label}包含禁止标签。`);
  }
  if (/\son[a-z]+\s*=|javascript\s*:/i.test(value)) {
    throw new ContentContractError(`${label}包含可执行内容。`);
  }
}

function assertText(value: string, label: string): void {
  if (value.trim().length === 0) throw new ContentContractError(`${label}不能为空。`);
}

function stableHash(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}
