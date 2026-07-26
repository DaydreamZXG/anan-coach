import { describe, expect, it } from "vitest";
import type { AnnotationStroke } from "../miniprogram/task-runtime/contracts";
import {
  appendStrokeWithinLimit,
  capturedPoint,
  isErasedStroke,
  liveStrokes,
  MAX_ANNOTATION_STROKES,
  normalizedPressure,
  shouldRejectAsPalm,
  strokesVisibleAtReplay,
  tombstoneStrokes,
  WECHAT_IOS_MAX_FORCE,
} from "../miniprogram/task-runtime/annotation-layer/strokes";

function stroke(strokeId: string, atMs: number, erasedAtMs?: number): AnnotationStroke {
  return {
    strokeId,
    tool: "pen",
    width: 0.006,
    points: [
      { x: 0.1, y: 0.1, t: 0 },
      { x: 0.2, y: 0.2, t: 16 },
    ],
    atMs,
    ...(erasedAtMs !== undefined ? { erasedAtMs } : {}),
  };
}

describe("annotation stroke tombstones (契约增量 v1)", () => {
  it("tombstones erased strokes without physically deleting them", () => {
    const original = [stroke("s-1", 100), stroke("s-2", 200)];
    const erased = tombstoneStrokes(original, (candidate) => candidate.strokeId === "s-1", 1_500);

    // 不物理删除：数量不变，命中的笔画只是盖上 erasedAtMs。
    expect(erased).toHaveLength(2);
    expect(erased[0]).toMatchObject({ strokeId: "s-1", erasedAtMs: 1_500 });
    expect(isErasedStroke(erased[0] as AnnotationStroke)).toBe(true);
    // 未命中的笔画保持同一引用；原数组不被改写。
    expect(erased[1]).toBe(original[1]);
    expect(original[0]?.erasedAtMs).toBeUndefined();

    // 已擦除的笔画不会被重复擦除（时间戳不被覆盖），无命中时返回原引用。
    const again = tombstoneStrokes(erased, () => true, 9_999);
    expect(again[0]).toMatchObject({ erasedAtMs: 1_500 });
    const untouched = tombstoneStrokes(erased, (candidate) => candidate.strokeId === "s-1", 9_999);
    expect(untouched).toBe(erased);
  });

  it("filters tombstones from live rendering and replays the erase moment", () => {
    const strokes = [stroke("s-live", 100), stroke("s-erased", 500, 900)];

    expect(liveStrokes(strokes).map((entry) => entry.strokeId)).toEqual(["s-live"]);

    // 回放：atMs 未到不出现；到了出现；replayMs >= erasedAtMs 时消失，还原擦除过程。
    expect(strokesVisibleAtReplay(strokes, 400).map((entry) => entry.strokeId)).toEqual(["s-live"]);
    expect(strokesVisibleAtReplay(strokes, 500).map((entry) => entry.strokeId)).toEqual([
      "s-live",
      "s-erased",
    ]);
    expect(strokesVisibleAtReplay(strokes, 899).map((entry) => entry.strokeId)).toEqual([
      "s-live",
      "s-erased",
    ]);
    expect(strokesVisibleAtReplay(strokes, 900).map((entry) => entry.strokeId)).toEqual(["s-live"]);
  });

  it("evicts the oldest erased stroke to make room at the 120-stroke cap", () => {
    const full: AnnotationStroke[] = [
      stroke("erased-late", 10, 300),
      stroke("erased-early", 20, 100),
      ...Array.from({ length: MAX_ANNOTATION_STROKES - 2 }, (_, index) =>
        stroke(`live-${String(index)}`, 30 + index),
      ),
    ];
    expect(full).toHaveLength(MAX_ANNOTATION_STROKES);

    const appended = appendStrokeWithinLimit(full, stroke("s-new", 5_000));
    expect(appended).toHaveLength(MAX_ANNOTATION_STROKES);
    const ids = (appended ?? []).map((entry) => entry.strokeId);
    // 腾位只物理删除最旧的已擦除笔画，其余（含较新的墓碑）全部保留。
    expect(ids).not.toContain("erased-early");
    expect(ids).toContain("erased-late");
    expect(ids).toContain("s-new");

    // 没到上限时直接追加，不做任何删除。
    const under = appendStrokeWithinLimit(full.slice(0, 10), stroke("s-tail", 6_000));
    expect(under).toHaveLength(11);

    // 全部是活笔画时没有可腾位的墓碑，返回 null 交由调用方提示上限。
    const allLive = Array.from({ length: MAX_ANNOTATION_STROKES }, (_, index) =>
      stroke(`live-${String(index)}`, index),
    );
    expect(appendStrokeWithinLimit(allLive, stroke("s-overflow", 7_000))).toBeNull();
  });
});

describe("annotation pressure capture (契约增量 v1)", () => {
  it("normalizes touch.force into p and omits the field on pressureless devices", () => {
    // 微信 iOS：force>0 按满压 3.3333 归一到 (0..1]，超出上限夹到 1。
    expect(normalizedPressure(WECHAT_IOS_MAX_FORCE)).toBe(1);
    expect(normalizedPressure(WECHAT_IOS_MAX_FORCE / 2)).toBeCloseTo(0.5, 6);
    expect(normalizedPressure(9)).toBe(1);
    // 安卓/无压感设备：force 缺省或为 0，不产生压感。
    expect(normalizedPressure(0)).toBeUndefined();
    expect(normalizedPressure(undefined)).toBeUndefined();

    // 写入：有压感时逐点带 p；无压感时不写该字段，旧载荷形状保持不变。
    const pressured = capturedPoint(
      { x: 0.1, y: 0.2 },
      16,
      normalizedPressure(WECHAT_IOS_MAX_FORCE),
    );
    expect(pressured).toEqual({ x: 0.1, y: 0.2, t: 16, p: 1 });
    const plain = capturedPoint({ x: 0.3, y: 0.4 }, 32, normalizedPressure(0));
    expect(plain).toEqual({ x: 0.3, y: 0.4, t: 32 });
    expect("p" in plain).toBe(false);
  });

  it("suppresses palm touches once a pressured stroke has started in the session", () => {
    // 会话内尚无 force>0 的先例：手指（无压感）照常落墨 —— 无 Pencil 的设备不受影响。
    let sessionSawPressure = false;
    expect(shouldRejectAsPalm(sessionSawPressure, normalizedPressure(0))).toBe(false);
    expect(shouldRejectAsPalm(sessionSawPressure, normalizedPressure(undefined))).toBe(false);

    // Apple Pencil 起笔（force>0）：正常落墨并置位先例标志。
    const pencil = normalizedPressure(1.2);
    expect(shouldRejectAsPalm(sessionSawPressure, pencil)).toBe(false);
    if (pencil !== undefined) sessionSawPressure = true;
    expect(sessionSawPressure).toBe(true);

    // 此后 force===0 的新触点视为手掌搁置，整笔忽略；Pencil 触点不受影响。
    expect(shouldRejectAsPalm(sessionSawPressure, normalizedPressure(0))).toBe(true);
    expect(shouldRejectAsPalm(sessionSawPressure, normalizedPressure(undefined))).toBe(true);
    expect(shouldRejectAsPalm(sessionSawPressure, normalizedPressure(2))).toBe(false);

    // 会话结束（组件 detached/退出批注模式）重置标志后，手指恢复落墨。
    sessionSawPressure = false;
    expect(shouldRejectAsPalm(sessionSawPressure, normalizedPressure(0))).toBe(false);
  });
});
