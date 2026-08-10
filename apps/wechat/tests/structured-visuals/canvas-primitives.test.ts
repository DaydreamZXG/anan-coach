import { describe, expect, it, vi } from "vitest";

import { paintStructuredVisualScene } from "../../miniprogram/design-system/structured-visuals/canvas";
import type { VisualScene } from "../../miniprogram/design-system/structured-visuals/scene";

describe("Canvas 2D primitive adapter", () => {
  it("uses native curves, ellipse arcs, styles, opacity, and controlled matrices", () => {
    const context = mockContext();
    const matrix = [1, 0.2, -0.1, 1, 8, 9] as const;
    const scene: VisualScene = {
      width: 100,
      height: 100,
      labelBoxes: [],
      missingValueCount: 0,
      commands: [
        {
          type: "quadraticBezier",
          from: { x: 0, y: 0 },
          control: { x: 5, y: 10 },
          to: { x: 10, y: 0 },
          stroke: "#101828",
          lineWidth: 2,
          dash: [3, 2],
          opacity: 0.5,
          matrix: [...matrix],
        },
        {
          type: "cubicBezier",
          from: { x: 0, y: 0 },
          control1: { x: 2, y: 8 },
          control2: { x: 8, y: 2 },
          to: { x: 10, y: 10 },
          stroke: "#101828",
          lineWidth: 1,
        },
        {
          type: "ellipse",
          center: { x: 50, y: 50 },
          radiusX: 20,
          radiusY: 10,
          rotationRadians: 0.3,
          fill: "rgba(16,24,40,0.2)",
          stroke: "#101828",
          lineWidth: 1,
        },
        {
          type: "arc",
          center: { x: 50, y: 50 },
          radiusX: 20,
          radiusY: 10,
          rotationRadians: 0,
          startAngle: 0,
          endAngle: Math.PI,
          anticlockwise: true,
          stroke: "#101828",
          lineWidth: 1,
        },
        {
          type: "roundedRect",
          box: { x: 10, y: 10, width: 30, height: 20 },
          radius: 4,
          fill: "#ffffff",
          stroke: "#101828",
          lineWidth: 1,
        },
      ],
    };

    paintStructuredVisualScene(context.value, scene);
    expect(context.methods.transform).toHaveBeenCalledWith(...matrix);
    expect(context.methods.quadraticCurveTo).toHaveBeenCalled();
    expect(context.methods.bezierCurveTo).toHaveBeenCalled();
    expect(context.methods.ellipse).toHaveBeenCalledTimes(2);
    expect(context.methods.quadraticCurveTo?.mock.calls.length ?? 0).toBeGreaterThanOrEqual(5);
    expect(context.methods.setLineDash).toHaveBeenCalledWith([3, 2]);
    expect(context.assignments).toContainEqual(["globalAlpha", 0.5]);
  });
});

function mockContext(): {
  value: Parameters<typeof paintStructuredVisualScene>[0];
  methods: Record<string, ReturnType<typeof vi.fn>>;
  assignments: Array<[string, unknown]>;
} {
  const methods: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const name of [
    "clearRect",
    "save",
    "restore",
    "transform",
    "setLineDash",
    "beginPath",
    "moveTo",
    "lineTo",
    "quadraticCurveTo",
    "bezierCurveTo",
    "ellipse",
    "arc",
    "closePath",
    "fill",
    "stroke",
    "fillRect",
    "strokeRect",
    "fillText",
  ])
    methods[name] = vi.fn();
  const assignments: Array<[string, unknown]> = [];
  const value = new Proxy(methods, {
    set(target, property, newValue) {
      assignments.push([String(property), newValue]);
      Reflect.set(target, property, newValue);
      return true;
    },
  });
  return {
    value: value as unknown as Parameters<typeof paintStructuredVisualScene>[0],
    methods,
    assignments,
  };
}
