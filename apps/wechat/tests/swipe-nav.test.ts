import { describe, expect, it } from "vitest";

import {
  defaultSwipeNavThresholds,
  resolveSwipeNav,
  swipeSampleFromTouchEnd,
  swipeSampleFromTouchStart,
  type SwipeNavSample,
} from "../miniprogram/task-runtime/swipe-nav";

function at(x: number, y: number, timeMs: number): SwipeNavSample {
  return { x, y, timeMs };
}

describe("swipe-nav 手势判定矩阵", () => {
  it("手指向左快滑判定为下一题，向右快滑判定为上一题", () => {
    expect(resolveSwipeNav(at(300, 400, 1_000), at(140, 408, 1_180))).toBe("next");
    expect(resolveSwipeNav(at(120, 400, 1_000), at(280, 392, 1_180))).toBe("prev");
  });

  it("横向位移必须严格超过 80px 高阈值", () => {
    expect(resolveSwipeNav(at(300, 400, 1_000), at(220, 400, 1_150))).toBeNull();
    expect(resolveSwipeNav(at(300, 400, 1_000), at(219, 400, 1_150))).toBe("next");
  });

  it("斜滑按纵向滚动处理：|dx| 不足 1.5 倍 |dy| 不切题", () => {
    // |dx|=120 恰好等于 1.5*|dy|=120：不占优，判空。
    expect(resolveSwipeNav(at(300, 400, 1_000), at(180, 480, 1_180))).toBeNull();
    expect(resolveSwipeNav(at(300, 400, 1_000), at(180, 479, 1_180))).toBe("next");
  });

  it("touchend 时间窗内横向速度不足（慢拖）不切题", () => {
    // 120px / 500ms = 0.24 px/ms < 0.3。
    expect(resolveSwipeNav(at(300, 400, 1_000), at(180, 400, 1_500))).toBeNull();
    // 120px / 300ms = 0.4 px/ms。
    expect(resolveSwipeNav(at(300, 400, 1_000), at(180, 400, 1_300))).toBe("next");
  });

  it("超出时间窗上限或时间倒流一律判空", () => {
    // 300px / 701ms 速度达标，但超过 maxDurationMs 时间窗仍不切题。
    const tooLate = 1_000 + defaultSwipeNavThresholds.maxDurationMs + 1;
    expect(resolveSwipeNav(at(300, 400, 1_000), at(0, 400, tooLate))).toBeNull();
    expect(resolveSwipeNav(at(300, 400, 1_000), at(140, 400, 1_000))).toBeNull();
    expect(resolveSwipeNav(at(300, 400, 1_000), at(140, 400, 900))).toBeNull();
  });

  it("起止触点缺失或坐标非法直接判空", () => {
    expect(resolveSwipeNav(null, at(140, 400, 1_200))).toBeNull();
    expect(resolveSwipeNav(at(300, 400, 1_000), null)).toBeNull();
    expect(resolveSwipeNav(at(Number.NaN, 400, 1_000), at(140, 400, 1_200))).toBeNull();
  });

  it("阈值可注入：放宽后同一手势可以命中", () => {
    const relaxed = {
      minDistancePx: 40,
      axisRatio: 1.2,
      minVelocityPxPerMs: 0.1,
      maxDurationMs: 1_000,
    };
    const start = at(300, 400, 1_000);
    const end = at(240, 440, 1_600);
    expect(resolveSwipeNav(start, end)).toBeNull();
    expect(resolveSwipeNav(start, end, relaxed)).toBe("next");
  });
});

describe("swipe-nav 触点采样", () => {
  it("touchstart 单指取 clientX/clientY 与事件时间戳", () => {
    expect(
      swipeSampleFromTouchStart({
        timeStamp: 5_000,
        touches: [{ clientX: 210, clientY: 320 }],
        changedTouches: [{ clientX: 210, clientY: 320 }],
      }),
    ).toEqual({ x: 210, y: 320, timeMs: 5_000 });
  });

  it("touchstart 多指（缩放/双指滚动）判空", () => {
    expect(
      swipeSampleFromTouchStart({
        timeStamp: 5_000,
        touches: [
          { clientX: 210, clientY: 320 },
          { clientX: 260, clientY: 340 },
        ],
      }),
    ).toBeNull();
  });

  it("touchend 终点从 changedTouches 读取，缺 clientX 时退回 pageX", () => {
    expect(
      swipeSampleFromTouchEnd({
        timeStamp: 5_200,
        touches: [],
        changedTouches: [{ pageX: 90, pageY: 310 }],
      }),
    ).toEqual({ x: 90, y: 310, timeMs: 5_200 });
    expect(
      swipeSampleFromTouchEnd({ timeStamp: 5_200, touches: [], changedTouches: [] }),
    ).toBeNull();
  });
});
