import type { AnnotationStroke } from "../contracts";

/**
 * 契约增量 v1：擦除墓碑的纯逻辑。
 * 橡皮/套索不再物理删除笔画，改为盖 erasedAtMs；渲染与回放据此过滤。
 * 120 笔/题上限含墓碑；达到上限时允许物理删除最旧的已擦除笔画腾位。
 */

/** 与后端 AnswerCheckpoint.annotations 的上限一致（含墓碑）。 */
export const MAX_ANNOTATION_STROKES = 120;

export function isErasedStroke(stroke: AnnotationStroke): boolean {
  return stroke.erasedAtMs !== undefined;
}

/** 作答态可见笔画：过滤掉已被擦除（墓碑）的笔画。 */
export function liveStrokes(strokes: AnnotationStroke[]): AnnotationStroke[] {
  return strokes.filter((stroke) => !isErasedStroke(stroke));
}

/** 回放态可见笔画：atMs 已到、且尚未走到擦除时刻（replayMs >= erasedAtMs 时消失）。 */
export function strokesVisibleAtReplay(
  strokes: AnnotationStroke[],
  replayMs: number,
): AnnotationStroke[] {
  return strokes.filter(
    (stroke) =>
      stroke.atMs <= replayMs && (stroke.erasedAtMs === undefined || replayMs < stroke.erasedAtMs),
  );
}

/** 给命中的活笔画盖擦除墓碑；没有命中时原样返回同一个数组引用。 */
export function tombstoneStrokes(
  strokes: AnnotationStroke[],
  shouldErase: (stroke: AnnotationStroke) => boolean,
  erasedAtMs: number,
): AnnotationStroke[] {
  const next = strokes.map((stroke) =>
    isErasedStroke(stroke) || !shouldErase(stroke) ? stroke : { ...stroke, erasedAtMs },
  );
  return next.some((stroke, index) => stroke !== strokes[index]) ? next : strokes;
}

/**
 * 在 120 笔上限内追加一笔：满员时物理删除最旧的已擦除笔画腾位；
 * 没有可腾位的墓碑（全是活笔画）时返回 null，由调用方提示上限。
 */
export function appendStrokeWithinLimit(
  strokes: AnnotationStroke[],
  stroke: AnnotationStroke,
  maxStrokes = MAX_ANNOTATION_STROKES,
): AnnotationStroke[] | null {
  let base = strokes;
  while (base.length >= maxStrokes) {
    const oldest = oldestErasedStroke(base);
    if (!oldest) return null;
    base = base.filter((candidate) => candidate !== oldest);
  }
  return [...base, stroke];
}

function oldestErasedStroke(strokes: AnnotationStroke[]): AnnotationStroke | null {
  let oldest: AnnotationStroke | null = null;
  for (const stroke of strokes) {
    if (!isErasedStroke(stroke)) continue;
    if (
      !oldest ||
      (stroke.erasedAtMs ?? 0) < (oldest.erasedAtMs ?? 0) ||
      ((stroke.erasedAtMs ?? 0) === (oldest.erasedAtMs ?? 0) && stroke.atMs < oldest.atMs)
    ) {
      oldest = stroke;
    }
  }
  return oldest;
}
