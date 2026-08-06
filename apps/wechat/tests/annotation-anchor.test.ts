import { describe, expect, it } from "vitest";
import type { AnnotationStroke } from "../miniprogram/task-runtime/contracts";
import type { RichContentBlock } from "../miniprogram/pages/practice/shared/task-runtime/content-renderer/model";
import {
  ANCHOR_MATCH_DISTANCE,
  collectAnchorSegments,
  normalizeSegmentRects,
  resolveStrokeAnchor,
  translatedPoints,
  type AnchorSegmentRect,
  type NormalizedSegmentRect,
} from "../miniprogram/pages/practice/shared/task-runtime/annotation-layer/anchor";

function block(blockId: string, text: string): RichContentBlock {
  return { type: "paragraph", blockId, text };
}

function anchoredStroke(points: AnnotationStroke["points"]): AnnotationStroke {
  return { strokeId: "s-1", tool: "highlighter", width: 0.03, points, atMs: 0 };
}

describe("annotation anchor segmentation (契约增量 v2)", () => {
  it("derives anchor segments from selectable paragraph text, skipping html/other kinds", () => {
    const blocks = [
      block("q1-stem", "甲说第一句。乙说第二句？"),
      { type: "paragraph", blockId: "q1-html", text: "忽略我", html: "<p>忽略我</p>" },
      {
        type: "image",
        assetId: "a",
        url: "/x.png",
        originalOcrUrl: "",
        altText: "图",
        zoomable: true,
      },
      block("q1-tail", "没有标点的一段"),
    ] as RichContentBlock[];

    const segments = collectAnchorSegments(blocks);
    expect(segments).toEqual([
      { blockId: "q1-stem", segmentIndex: 0, segmentText: "甲说第一句。" },
      { blockId: "q1-stem", segmentIndex: 1, segmentText: "乙说第二句？" },
      { blockId: "q1-tail", segmentIndex: 0, segmentText: "没有标点的一段" },
    ]);
  });

  it("normalizes host viewport rects into content coordinates (scroll-invariant)", () => {
    const layer = { left: 10, top: 20, width: 200, height: 400 };
    const contentHeight = 1000;
    const scrollTop = 300;
    const rects: AnchorSegmentRect[] = [
      {
        blockId: "q1-stem",
        segmentIndex: 0,
        segmentText: "甲说第一句。",
        left: 30,
        top: 120,
        width: 80,
        height: 24,
      },
    ];
    const normalized = normalizeSegmentRects(rects, layer, scrollTop, contentHeight);
    expect(normalized[0]).toMatchObject({
      blockId: "q1-stem",
      segmentIndex: 0,
      segmentText: "甲说第一句。",
      x: (30 - 10) / 200,
      y: (120 - 20 + scrollTop) / contentHeight,
      width: 80 / 200,
      height: 24 / contentHeight,
    });

    // 内容高未就绪时归一化失败 → 空表（调用方按无锚定处理）。
    expect(normalizeSegmentRects(rects, layer, 0, 0)).toEqual([]);
  });

  it("resolves a stroke to its nearest segment within the match distance", () => {
    const segments: NormalizedSegmentRect[] = [
      {
        blockId: "q1-stem",
        segmentIndex: 0,
        segmentText: "甲说第一句。",
        x: 0.1,
        y: 0.1,
        width: 0.3,
        height: 0.05,
      },
      {
        blockId: "q1-stem",
        segmentIndex: 1,
        segmentText: "乙说第二句？",
        x: 0.1,
        y: 0.2,
        width: 0.3,
        height: 0.05,
      },
    ];
    // 划在第二句下方约 0.02（在命中距离内），应命中第二句。
    const resolved = resolveStrokeAnchor(
      anchoredStroke([
        { x: 0.12, y: 0.255, t: 0 },
        { x: 0.35, y: 0.255, t: 16 },
      ]),
      segments,
    );
    expect(resolved).toMatchObject({
      blockId: "q1-stem",
      segmentIndex: 1,
      segmentText: "乙说第二句？",
    });
  });

  it("prefers the containing segment when the stroke starts inside one (two-line highlight)", () => {
    const segments: NormalizedSegmentRect[] = [
      {
        blockId: "q1-stem",
        segmentIndex: 0,
        segmentText: "甲说第一句。",
        x: 0.1,
        y: 0.1,
        width: 0.3,
        height: 0.05,
      },
      {
        blockId: "q1-stem",
        segmentIndex: 1,
        segmentText: "乙说第二句？",
        x: 0.1,
        y: 0.2,
        width: 0.3,
        height: 0.05,
      },
    ];
    // 起点落在第二句内、终点落到下一行（都更接近第二句下缘）——归属起笔所在句段。
    const resolved = resolveStrokeAnchor(
      anchoredStroke([
        { x: 0.2, y: 0.22, t: 0 },
        { x: 0.3, y: 0.28, t: 16 },
      ]),
      segments,
    );
    expect(resolved?.segmentIndex).toBe(1);
  });

  it("returns null when no segment is within the match distance (plain scribble)", () => {
    const segments: NormalizedSegmentRect[] = [
      {
        blockId: "q1-stem",
        segmentIndex: 0,
        segmentText: "甲说第一句。",
        x: 0.1,
        y: 0.1,
        width: 0.3,
        height: 0.05,
      },
    ];
    const resolved = resolveStrokeAnchor(
      anchoredStroke([
        { x: 0.8, y: 0.8, t: 0 },
        { x: 0.9, y: 0.85, t: 16 },
      ]),
      segments,
    );
    expect(resolved).toBeNull();
    // 空句段表也返回 null，与"旧笔迹无锚定"语义一致。
    expect(resolveStrokeAnchor(anchoredStroke([{ x: 0.5, y: 0.5, t: 0 }]), [])).toBeNull();
  });

  it("translates points by the segment centroid displacement for scroll following", () => {
    const from: NormalizedSegmentRect = {
      blockId: "q1-stem",
      segmentIndex: 1,
      segmentText: "乙",
      x: 0.1,
      y: 0.2,
      width: 0.3,
      height: 0.05,
    };
    const to: NormalizedSegmentRect = {
      blockId: "q1-stem",
      segmentIndex: 1,
      segmentText: "乙",
      x: 0.1,
      y: 0.1,
      width: 0.3,
      height: 0.05,
    };
    const stroke = anchoredStroke([
      { x: 0.12, y: 0.255, t: 0, p: 0.5 },
      { x: 0.35, y: 0.255, t: 16 },
    ]);
    const moved = translatedPoints(stroke, from, to);
    // dy = (0.1+0.025) - (0.2+0.025) = -0.1：y 上移 0.1，x 不变。
    expect(moved[0]).toEqual({ x: 0.12, y: 0.155, t: 0, p: 0.5 });
    expect(moved[1]).toEqual({ x: 0.35, y: 0.155, t: 16 });
    // 段未位移时原样返回同一引用（避免无意义重绘）。
    expect(translatedPoints(stroke, from, from)).toBe(stroke.points);
  });

  it("is tolerant of the documented match-distance constant", () => {
    // ANCHOR_MATCH_DISTANCE 是对比阈值，必须为合理的小正数（覆盖划线/下划线/圈画）。
    expect(ANCHOR_MATCH_DISTANCE).toBeGreaterThan(0);
    expect(ANCHOR_MATCH_DISTANCE).toBeLessThan(0.25);
  });
});
