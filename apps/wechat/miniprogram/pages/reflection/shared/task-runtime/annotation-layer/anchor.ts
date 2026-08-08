import type { AnnotationPoint, AnnotationStroke } from "../../../../../task-runtime/contracts";
import { segmentSelectableText, type RichContentBlock } from "../content-renderer/model";

/**
 * 契约增量 v2：把笔迹解析成"用户标记了哪句原文"的纯几何逻辑。
 *
 * 坐标系约定：宿主（练习页）提供题干句段在视口坐标系下的 boundingClientRect
 * （像素），批注层用自己画布的 rect 归一化成 0..1（与笔迹坐标同一坐标系）。
 * 笔迹提交时做最近句段命中 → 给笔画盖 anchor{blockId, segmentIndex, segmentText}；
 * 宿主在题目滚动/切题后重传句段矩形，批注层按"段从 R0 移到 R1"的位移平移
 * 已锚定笔迹，让荧光笔/圈画跟着原文走（路由 B：锚点重算 + 重绘）。
 */

export interface AnchorSegmentMeta {
  blockId: string;
  segmentIndex: number;
  segmentText: string;
}

/** 宿主传入的句段视口矩形（boundingClientRect，像素）。 */
export interface AnchorSegmentRect extends AnchorSegmentMeta {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** 归一化到批注层（0..1）的句段矩形，与笔迹坐标同一坐标系。 */
export interface NormalizedSegmentRect extends AnchorSegmentMeta {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface HorizontalAnnotationRegion {
  blockId: string;
  label: string;
  viewportLeft: number;
  viewportTop: number;
  viewportWidth: number;
  viewportHeight: number;
  contentWidth: number;
  scrollLeft: number;
  measuredScrollTop: number;
}

export interface AnnotationCanvasMetrics {
  left: number;
  top: number;
  width: number;
  contentHeight: number;
  scrollTop: number;
}

export function horizontalRegionAtPoint(
  regions: readonly HorizontalAnnotationRegion[],
  metrics: AnnotationCanvasMetrics,
  clientX: number,
  clientY: number,
): HorizontalAnnotationRegion | null {
  const contentY = clientY - metrics.top + metrics.scrollTop;
  return (
    regions.find((region) => {
      const contentTop = region.viewportTop - metrics.top + region.measuredScrollTop;
      const contentBottom = contentTop + region.viewportHeight;
      return (
        clientX >= region.viewportLeft &&
        clientX <= region.viewportLeft + region.viewportWidth &&
        contentY >= contentTop &&
        contentY <= contentBottom
      );
    }) ?? null
  );
}

export function capturedHorizontalX(region: HorizontalAnnotationRegion, clientX: number): number {
  if (region.contentWidth <= 0) return 0;
  return clamp((clientX - region.viewportLeft + region.scrollLeft) / region.contentWidth);
}

export function renderedHorizontalX(
  region: HorizontalAnnotationRegion,
  metrics: Pick<AnnotationCanvasMetrics, "left">,
  normalizedX: number,
): number {
  return (
    region.viewportLeft -
    metrics.left +
    clamp(normalizedX) * region.contentWidth -
    region.scrollLeft
  );
}

function clamp(value: number): number {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}

/** 笔迹离句段多远内算命中；0.1 ≈ 画面高度的一成，宽容覆盖划线/下划线/圈画。 */
export const ANCHOR_MATCH_DISTANCE = 0.1;

export interface ResolvedAnchor {
  blockId: string;
  segmentIndex: number;
  segmentText: string;
  /** 命中句段的归一化矩形，作为该笔滚动平移的起点（R0）。 */
  r0: NormalizedSegmentRect;
}

/** 由富文本块推导可锚定句段元数据（只取无 HTML 的段落块，与渲染器 selectable 分支一致）。 */
export function collectAnchorSegments(blocks: readonly RichContentBlock[]): AnchorSegmentMeta[] {
  const segments: AnchorSegmentMeta[] = [];
  for (const block of blocks) {
    if (block.type !== "paragraph" || block.html) continue;
    segmentSelectableText(block.text).forEach((segmentText, segmentIndex) => {
      segments.push({ blockId: block.blockId, segmentIndex, segmentText });
    });
  }
  return segments;
}

/**
 * 把宿主上报的句段视口矩形转成内容坐标系（0..1 相对整个题目内容高度）。
 * 内容坐标 = (视口 top + scrollTop) / contentHeight —— 滚动只影响 scrollTop，
 * 内容坐标恒定，笔迹/命中/滚动天然稳定，不需要任何位移补偿。
 * contentHeight 未就绪时返回空表。
 */
export function normalizeSegmentRects(
  rects: readonly AnchorSegmentRect[],
  layer: { left: number; top: number; width: number; height: number },
  scrollTop: number,
  contentHeight: number,
): NormalizedSegmentRect[] {
  if (layer.width <= 0 || contentHeight <= 0) return [];
  return rects.map((rect) => ({
    blockId: rect.blockId,
    segmentIndex: rect.segmentIndex,
    segmentText: rect.segmentText,
    x: (rect.left - layer.left) / layer.width,
    y: (rect.top - layer.top + scrollTop) / contentHeight,
    width: rect.width / layer.width,
    height: rect.height / contentHeight,
  }));
}

/**
 * 最近句段命中：取笔迹各点到句段的最近距离，命中阈值内的最近段为锚定目标。
 * 若笔迹起点落在某个句段内，优先选该段（跨两行的荧光笔归属起笔那一行）。
 * 没有命中 → 返回 null，笔画视为普通涂画（旧笔迹同义）。
 */
export function resolveStrokeAnchor(
  stroke: AnnotationStroke,
  segments: readonly NormalizedSegmentRect[],
): ResolvedAnchor | null {
  if (!segments.length) return null;
  let best: { segment: NormalizedSegmentRect; distance: number } | null = null;
  for (const segment of segments) {
    const distance = minPointDistanceToRect(stroke.points, segment);
    if (distance <= ANCHOR_MATCH_DISTANCE && (!best || distance < best.distance)) {
      best = { segment, distance };
    }
  }
  if (!best) return null;
  const first = stroke.points[0];
  const containing = first
    ? segments.find((segment) => pointInRect(first.x, first.y, segment))
    : undefined;
  const segment = containing ?? best.segment;
  return {
    blockId: segment.blockId,
    segmentIndex: segment.segmentIndex,
    segmentText: segment.segmentText,
    r0: segment,
  };
}

/** 把笔迹按"段从 from 移到 to"的位移平移（滚动跟随）；位移为零时原样返回。 */
export function translatedPoints(
  stroke: AnnotationStroke,
  from: NormalizedSegmentRect,
  to: NormalizedSegmentRect,
): AnnotationPoint[] {
  const dx = to.x + to.width / 2 - (from.x + from.width / 2);
  const dy = to.y + to.height / 2 - (from.y + from.height / 2);
  if (Math.abs(dx) < 1e-6 && Math.abs(dy) < 1e-6) return stroke.points;
  return stroke.points.map((point) => ({
    x: point.x + dx,
    y: point.y + dy,
    ...(point.t !== undefined ? { t: point.t } : {}),
    ...(point.p !== undefined ? { p: point.p } : {}),
  }));
}

function minPointDistanceToRect(
  points: readonly AnnotationPoint[],
  rect: NormalizedSegmentRect,
): number {
  let best = Infinity;
  for (const point of points) {
    const dx = Math.max(rect.x - point.x, point.x - (rect.x + rect.width), 0);
    const dy = Math.max(rect.y - point.y, point.y - (rect.y + rect.height), 0);
    const distance = Math.hypot(dx, dy);
    if (distance < best) best = distance;
  }
  return best;
}

function pointInRect(x: number, y: number, rect: NormalizedSegmentRect): boolean {
  return x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
}
