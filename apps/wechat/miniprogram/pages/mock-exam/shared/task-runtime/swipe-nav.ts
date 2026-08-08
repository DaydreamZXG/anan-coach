/**
 * 左右快滑切题的手势判定（纯函数，便于矩阵化单测）。
 *
 * 判定原则：纵向滚动优先，宁可漏判不可误判——
 * - 横向位移必须严格大于 minDistancePx（80px 高阈值）；
 * - 横向必须显著占优（|dx| > axisRatio * |dy|），斜滑一律当作滚动；
 * - touchend 时间窗内要有足够横向平均速度，慢速拖动不切题。
 *
 * 已知边界：content-renderer 内的表格自身可横向滚动，其滚动手势通常
 * 慢且短，依赖高阈值 + 横向占优 + 速度判定降低冲突；表格上的一次
 * 快速长横滑仍可能切题，属于当前方案（非连续跟手 swiper）的可接受折衷。
 */

export interface SwipeNavSample {
  x: number;
  y: number;
  timeMs: number;
}

export type SwipeNavDirection = "prev" | "next";

export interface SwipeGestureState {
  start: SwipeNavSample | null;
  latest: SwipeNavSample | null;
}

export interface SwipeNavThresholds {
  /** 横向位移下限（px，严格大于才算命中）。 */
  minDistancePx: number;
  /** 横向占优比：|dx| 必须严格大于 axisRatio * |dy|。 */
  axisRatio: number;
  /** 横向平均速度下限（px/ms），touchend 时间窗内不够快不算。 */
  minVelocityPxPerMs: number;
  /** 手势总时长上限（ms），超时按普通拖动处理。 */
  maxDurationMs: number;
}

export const defaultSwipeNavThresholds: SwipeNavThresholds = {
  minDistancePx: 80,
  axisRatio: 1.5,
  minVelocityPxPerMs: 0.3,
  maxDurationMs: 700,
};

/**
 * 由起止触点判定切题方向：手指向左快滑（dx<0）＝看下一题，向右快滑
 * （dx>0）＝回上一题（主流刷题 App 的肌肉记忆）。不满足任一阈值返回 null。
 */
export function resolveSwipeNav(
  start: SwipeNavSample | null | undefined,
  end: SwipeNavSample | null | undefined,
  thresholds: SwipeNavThresholds = defaultSwipeNavThresholds,
): SwipeNavDirection | null {
  if (!start || !end) return null;
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const durationMs = end.timeMs - start.timeMs;
  if (!Number.isFinite(dx) || !Number.isFinite(dy) || !Number.isFinite(durationMs)) return null;
  if (durationMs <= 0 || durationMs > thresholds.maxDurationMs) return null;
  const distance = Math.abs(dx);
  if (distance <= thresholds.minDistancePx) return null;
  if (distance <= thresholds.axisRatio * Math.abs(dy)) return null;
  if (distance / durationMs < thresholds.minVelocityPxPerMs) return null;
  return dx < 0 ? "next" : "prev";
}

/** 触点的宽松结构：真机事件带 clientX/clientY，测试桩可能只给 pageX/pageY。 */
export interface SwipeNavTouchLike {
  clientX?: number;
  clientY?: number;
  pageX?: number;
  pageY?: number;
}

export interface SwipeNavTouchEventLike {
  timeStamp?: number;
  touches?: SwipeNavTouchLike[];
  changedTouches?: SwipeNavTouchLike[];
}

/** touchstart 采样；多指（表格缩放/双指滚动）直接放弃本次手势。 */
export function swipeSampleFromTouchStart(event: SwipeNavTouchEventLike): SwipeNavSample | null {
  if ((event.touches?.length ?? 0) > 1) return null;
  return sampleFromTouch(event.touches?.[0] ?? event.changedTouches?.[0], event.timeStamp);
}

/**
 * touchmove 采样；scroll-view 开始原生滚动后可能以 touchcancel 而非 touchend
 * 收尾，因此页面必须保留最后一个可用移动点，不能只依赖结束事件。
 */
export function swipeSampleFromTouchMove(event: SwipeNavTouchEventLike): SwipeNavSample | null {
  if ((event.touches?.length ?? 0) > 1) return null;
  return sampleFromTouch(event.touches?.[0] ?? event.changedTouches?.[0], event.timeStamp);
}

/** touchend 采样；结束事件的终点在 changedTouches 里。 */
export function swipeSampleFromTouchEnd(event: SwipeNavTouchEventLike): SwipeNavSample | null {
  return sampleFromTouch(event.changedTouches?.[0] ?? event.touches?.[0], event.timeStamp);
}

export const idleSwipeGesture = (): SwipeGestureState => ({ start: null, latest: null });

export function beginSwipeGesture(event: SwipeNavTouchEventLike): SwipeGestureState {
  const sample = swipeSampleFromTouchStart(event);
  return { start: sample, latest: sample };
}

export function moveSwipeGesture(
  state: SwipeGestureState,
  event: SwipeNavTouchEventLike,
): SwipeGestureState {
  if (!state.start) return state;
  if ((event.touches?.length ?? 0) > 1) return idleSwipeGesture();
  return { ...state, latest: swipeSampleFromTouchMove(event) ?? state.latest };
}

export function finishSwipeGesture(
  state: SwipeGestureState,
  event: SwipeNavTouchEventLike & { type?: string },
): { state: SwipeGestureState; direction: SwipeNavDirection | null } {
  const end =
    event.type === "touchcancel" ? state.latest : (swipeSampleFromTouchEnd(event) ?? state.latest);
  return {
    state: idleSwipeGesture(),
    direction: resolveSwipeNav(state.start, end),
  };
}

function sampleFromTouch(
  touch: SwipeNavTouchLike | undefined,
  timeStamp: number | undefined,
): SwipeNavSample | null {
  const x = touch?.clientX ?? touch?.pageX;
  const y = touch?.clientY ?? touch?.pageY;
  if (typeof x !== "number" || typeof y !== "number") return null;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  // 起止两端必须用同一时间基准：真机统一走 event.timeStamp，仅在缺失时退回 Date.now()。
  const timeMs =
    typeof timeStamp === "number" && Number.isFinite(timeStamp) ? timeStamp : Date.now();
  return { x, y, timeMs };
}
