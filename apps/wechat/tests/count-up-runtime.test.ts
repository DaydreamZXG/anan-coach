import { describe, expect, it } from "vitest";

import {
  COUNT_UP_FRAME_INTERVAL_MS,
  COUNT_UP_MAX_DURATION_MS,
  COUNT_UP_MIN_DURATION_MS,
  clampCountUpDuration,
  countUpFrames,
} from "../miniprogram/design-system/primitives/count-up/runtime";

describe("count-up 数字滚动运行时", () => {
  it("把时长钳制在动效纪律允许的 150-300ms 区间", () => {
    expect(clampCountUpDuration(1000)).toBe(COUNT_UP_MAX_DURATION_MS);
    expect(clampCountUpDuration(10)).toBe(COUNT_UP_MIN_DURATION_MS);
    expect(clampCountUpDuration(200)).toBe(200);
    expect(clampCountUpDuration(Number.NaN)).toBe(COUNT_UP_MAX_DURATION_MS);
  });

  it("帧序列按步进间隔铺满时长，末帧精确落在目标值", () => {
    const frames = countUpFrames(0, 127, 300);
    expect(frames).toHaveLength(Math.round(300 / COUNT_UP_FRAME_INTERVAL_MS));
    expect(frames.at(-1)).toBe(127);
    const shortFrames = countUpFrames(0, 127, 150);
    expect(shortFrames).toHaveLength(Math.round(150 / COUNT_UP_FRAME_INTERVAL_MS));
    expect(shortFrames.at(-1)).toBe(127);
  });

  it("上行单调不减、缓动前快后慢（ease-out），整数端点不滚出小数", () => {
    const frames = countUpFrames(0, 127, 300);
    for (const [index, frame] of frames.entries()) {
      expect(Number.isInteger(frame)).toBe(true);
      if (index > 0) expect(frame).toBeGreaterThanOrEqual(frames[index - 1] ?? 0);
    }
    const midpoint = frames[Math.floor(frames.length / 2) - 1] ?? 0;
    expect(midpoint).toBeGreaterThan(127 / 2);
  });

  it("支持下行滚动与小数端点，精度跟随端点且不超过两位", () => {
    const downward = countUpFrames(10, 0, 300);
    expect(downward.at(-1)).toBe(0);
    for (const [index, frame] of downward.entries()) {
      if (index > 0) expect(frame).toBeLessThanOrEqual(downward[index - 1] ?? 0);
    }
    const fractional = countUpFrames(0, 3.5, 300);
    expect(fractional.at(-1)).toBe(3.5);
    for (const frame of fractional) {
      expect(Math.round(frame * 10) / 10).toBe(frame);
    }
  });

  it("旧值等于新值时返回空序列，交由组件直接结算", () => {
    expect(countUpFrames(42, 42, 300)).toEqual([]);
  });
});
