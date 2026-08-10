import { describe, expect, it } from "vitest";

import {
  multiplyMatrices,
  primitiveTransformMatrix,
} from "../../miniprogram/design-system/structured-visuals/primitive-scene";
import { buildStructuredVisualScene } from "../../miniprogram/design-system/structured-visuals/scene";
import { validateStructuredVisual } from "../../miniprogram/design-system/structured-visuals/schema";
import { fixturePaths, reportFixture } from "./fixtures";

describe("responsive primitive scenes", () => {
  it.each([343, 680])(
    "keeps six real classification panels and internal matrices at %ipx",
    (width) => {
      const validation = validateStructuredVisual(
        reportFixture(fixturePaths.primitiveClassification),
      );
      expect(validation.status).toBe("valid");
      if (validation.status !== "valid") return;
      const scene = buildStructuredVisualScene(validation.visual, width);
      const frames = scene.commands.filter(
        (command) => command.type === "rect" && command.matrix === undefined,
      );
      expect(frames).toHaveLength(6);
      expect(scene.commands.filter((command) => command.type === "ellipse")).not.toHaveLength(0);
      expect(scene.commands.filter((command) => command.type === "arc")).not.toHaveLength(0);
      expect(
        scene.commands.filter((command) => command.matrix !== undefined).length,
      ).toBeGreaterThan(20);
      expect(scene.width).toBe(width);
    },
  );

  it.each([343, 680])("retains real glyph text and four options at %ipx", (width) => {
    for (const path of [fixturePaths.glyphSequence, fixturePaths.chineseGlyphSequence]) {
      const validation = validateStructuredVisual(reportFixture(path));
      expect(validation.status).toBe("valid");
      if (validation.status !== "valid") continue;
      const scene = buildStructuredVisualScene(validation.visual, width);
      const text = scene.commands
        .filter((command) => command.type === "text")
        .map((command) => command.text);
      expect(text).toContain("?");
      expect(text).toEqual(expect.arrayContaining(["A", "B", "C", "D"]));
      expect(scene.commands.filter((command) => command.type === "rect")).toHaveLength(
        path === fixturePaths.glyphSequence ? 9 : 10,
      );
    }
  });

  it("composes translation, rotation, scale, and origin deterministically", () => {
    const local = primitiveTransformMatrix({
      translateX: 10,
      translateY: 20,
      scaleX: 2,
      scaleY: 3,
      rotateDegrees: 90,
      originX: 5,
      originY: 6,
    });
    const composed = multiplyMatrices([2, 0, 0, 2, 100, 50], local);
    expect(composed.map((value) => Math.round(value * 1_000) / 1_000)).toEqual([
      0, 4, -6, 0, 166, 82,
    ]);
  });

  it("is deterministic for both phone and tablet scenes", () => {
    const validation = validateStructuredVisual(
      reportFixture(fixturePaths.primitiveClassification),
    );
    if (validation.status !== "valid") throw new Error("Expected valid real sample.");
    for (const width of [343, 680]) {
      expect(buildStructuredVisualScene(validation.visual, width)).toEqual(
        buildStructuredVisualScene(validation.visual, width),
      );
    }
  });
});
