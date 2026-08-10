import { describe, expect, it } from "vitest";

import {
  parseSvgPath,
  SVG_PATH_LIMITS,
  svgArcToCenter,
} from "../../miniprogram/design-system/structured-visuals/path";

describe("safe SVG path normalization", () => {
  it("normalizes every supported absolute command and implicit repetitions", () => {
    const result = parseSvgPath(
      "M10 10 20 20 L30 30 35 35 H40 45 V50 55 C46 56 47 57 48 58 49 59 50 60 51 61 S52 62 53 63 Q54 64 55 65 T56 66 A7 8 15 0 1 70 71 9 10 0 1 0 80 81 Z",
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.instructions.map((instruction) => instruction.op)).toEqual([
      "move",
      "line",
      "line",
      "line",
      "line",
      "line",
      "line",
      "line",
      "cubic",
      "cubic",
      "cubic",
      "quadratic",
      "quadratic",
      "arc",
      "arc",
      "close",
    ]);
  });

  it("resolves relative commands and reflects smooth controls", () => {
    const result = parseSvgPath(
      "m10 10 c10 0 10 10 20 10 s10 10 20 0 q10 -10 20 0 t20 0 a10 5 0 1 1 20 0 z",
    );
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.instructions[2]).toEqual({
      op: "cubic",
      control1X: 40,
      control1Y: 20,
      control2X: 40,
      control2Y: 30,
      x: 50,
      y: 20,
    });
    expect(result.instructions[4]).toEqual({
      op: "quadratic",
      controlX: 80,
      controlY: 30,
      x: 90,
      y: 20,
    });
    expect(result.instructions[5]).toMatchObject({
      op: "arc",
      largeArc: true,
      sweep: true,
      x: 110,
      y: 20,
    });
  });

  it("handles repeated relative line, horizontal, and vertical parameter sets", () => {
    const result = parseSvgPath("m1 2 l3 4 5 6 h2 3 v4 5");
    expect(result).toMatchObject({
      ok: true,
      instructions: [
        { op: "move", x: 1, y: 2 },
        { op: "line", x: 4, y: 6 },
        { op: "line", x: 9, y: 12 },
        { op: "line", x: 11, y: 12 },
        { op: "line", x: 14, y: 12 },
        { op: "line", x: 14, y: 16 },
        { op: "line", x: 14, y: 21 },
      ],
    });
  });

  it("preserves large-arc and sweep direction in center conversion", () => {
    const small = parseSingleArc("M0 0 A40 25 20 0 1 60 10");
    const large = parseSingleArc("M0 0 A40 25 20 1 1 60 10");
    const reverse = parseSingleArc("M0 0 A40 25 20 1 0 60 10");
    const smallCenter = svgArcToCenter(0, 0, small);
    const largeCenter = svgArcToCenter(0, 0, large);
    const reverseCenter = svgArcToCenter(0, 0, reverse);
    expect(Math.abs((smallCenter?.endAngle ?? 0) - (smallCenter?.startAngle ?? 0))).toBeLessThan(
      Math.PI,
    );
    expect(Math.abs((largeCenter?.endAngle ?? 0) - (largeCenter?.startAngle ?? 0))).toBeGreaterThan(
      Math.PI,
    );
    expect(largeCenter?.anticlockwise).toBe(false);
    expect(reverseCenter?.anticlockwise).toBe(true);
  });

  it("rejects malformed flags, unsupported data, non-finite values, and complexity overflow", () => {
    expect(parseSvgPath("M0 0 A5 5 0 2 0 10 10")).toMatchObject({
      ok: false,
      code: "PATH_ARC_FLAG_INVALID",
    });
    expect(parseSvgPath("M0 0 R10 10")).toMatchObject({
      ok: false,
      code: "PATH_COMMAND_UNSUPPORTED",
    });
    expect(parseSvgPath("M0 0 L1e999 2")).toMatchObject({
      ok: false,
      code: "PATH_NUMBER_INVALID",
    });
    expect(parseSvgPath("M0 0 L100001 0")).toMatchObject({
      ok: false,
      code: "PATH_COORDINATE_LIMIT",
    });
    expect(
      parseSvgPath(`M0 0 ${"L1 1 ".repeat(SVG_PATH_LIMITS.maxInstructions + 1)}`),
    ).toMatchObject({
      ok: false,
      code: "PATH_INSTRUCTION_LIMIT",
    });
  });
});

function parseSingleArc(
  path: string,
): Extract<
  Extract<ReturnType<typeof parseSvgPath>, { ok: true }>["instructions"][number],
  { op: "arc" }
> {
  const result = parseSvgPath(path);
  expect(result.ok).toBe(true);
  if (!result.ok) throw new Error(result.message);
  const arc = result.instructions.find(
    (instruction): instruction is Extract<typeof instruction, { op: "arc" }> =>
      instruction.op === "arc",
  );
  if (!arc) throw new Error("Expected arc instruction.");
  return arc;
}
