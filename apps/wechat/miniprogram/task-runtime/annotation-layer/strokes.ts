import type { AnnotationPoint, AnnotationStroke } from "../contracts";

/**
 * 契约增量 v1：擦除墓碑与压感采集的纯逻辑。
 * 橡皮/套索不再物理删除笔画，改为盖 erasedAtMs；渲染与回放据此过滤。
 * 120 笔/题上限含墓碑；达到上限时允许物理删除最旧的已擦除笔画腾位。
 * 压感 p 归一化到 (0..1]；仅压感设备写入，其余设备不写该字段。
 */

/** 微信 iOS 下 touch.force 的满压上限（Apple Pencil 全压约 3.3333）。 */
export const WECHAT_IOS_MAX_FORCE = 3.3333;

/**
 * 契约增量 v1：把 touch.force 归一化为 AnnotationPoint.p。
 * force > 0（iOS 压感触点）时 p = min(force / 3.3333, 1)；
 * 安卓/无压感设备（force 缺省或为 0）返回 undefined，表示不写该字段。
 */
export function normalizedPressure(force: number | undefined): number | undefined {
  if (typeof force !== "number" || !Number.isFinite(force) || force <= 0) return undefined;
  return Math.min(force / WECHAT_IOS_MAX_FORCE, 1);
}

/** 采集一个批注点：写入相对起笔的毫秒偏移 t；有压感（pressure ≠ undefined）时附带 p。 */
export function capturedPoint(
  point: { x: number; y: number },
  t: number,
  pressure: number | undefined,
): AnnotationPoint {
  return pressure === undefined
    ? { x: point.x, y: point.y, t }
    : { x: point.x, y: point.y, t, p: pressure };
}

/**
 * 手掌误触抑制（启发式）：本次批注会话内一旦出现 force>0 的笔画起笔（说明用户
 * 在用 Apple Pencil 这类压感笔），force===0 的新触点视为手掌搁置，整笔不落墨。
 * 启发式边界：无 Pencil 的 iPad/手机用手指书写不受影响 —— 手指触点不会产生
 * force>0 的先例，sessionSawPressure 恒为 false；会话结束（组件 detached/
 * 退出批注模式）由调用方重置该标志。
 */
export function shouldRejectAsPalm(
  sessionSawPressure: boolean,
  pressure: number | undefined,
): boolean {
  return sessionSawPressure && pressure === undefined;
}

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
