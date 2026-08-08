/*
 * count-up 数字滚动的纯计算层。
 * 动效纪律：时长钳制在 150-300ms；缓动与 --ease-out 令牌同族（ease-out cubic）。
 * reduce-motion 的直接跳变由组件层负责，不进入帧序列。
 */

export const COUNT_UP_MIN_DURATION_MS = 150;
export const COUNT_UP_MAX_DURATION_MS = 300;
/** 逻辑层没有 requestAnimationFrame，用 ~30fps 的 setInterval 步进。 */
export const COUNT_UP_FRAME_INTERVAL_MS = 33;

export function clampCountUpDuration(duration: number): number {
  if (!Number.isFinite(duration)) return COUNT_UP_MAX_DURATION_MS;
  return Math.min(COUNT_UP_MAX_DURATION_MS, Math.max(COUNT_UP_MIN_DURATION_MS, duration));
}

function easeOutCubic(progress: number): number {
  return 1 - (1 - progress) ** 3;
}

function decimalsOf(value: number): number {
  if (Number.isInteger(value)) return 0;
  const text = String(value);
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : Math.min(text.length - dot - 1, 2);
}

/**
 * 从旧值到新值的逐帧显示序列（不含起点，末帧精确等于目标值）。
 * 帧值按端点精度取整，避免整数场景滚出小数。
 */
export function countUpFrames(from: number, to: number, durationMs: number): number[] {
  if (from === to) return [];
  const scale = 10 ** Math.max(decimalsOf(from), decimalsOf(to));
  const total = Math.max(
    1,
    Math.round(clampCountUpDuration(durationMs) / COUNT_UP_FRAME_INTERVAL_MS),
  );
  const frames: number[] = [];
  for (let step = 1; step < total; step += 1) {
    const eased = easeOutCubic(step / total);
    frames.push(Math.round((from + (to - from) * eased) * scale) / scale);
  }
  frames.push(to);
  return frames;
}
