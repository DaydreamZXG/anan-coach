import { describe, expect, it } from "vitest";

import {
  boxesOverlap,
  buildStructuredVisualScene,
  preferredCanvasHeight,
  type SceneLabelBox,
} from "../../miniprogram/design-system/structured-visuals/scene";
import {
  validateStructuredVisual,
  type ValidatedStructuredVisual,
} from "../../miniprogram/design-system/structured-visuals/schema";
import { clonedFixture, fixturePaths, reportFixture } from "./fixtures";

describe("structured visual deterministic scenes", () => {
  it.each([
    [fixturePaths.groupedBar, 343],
    [fixturePaths.groupedBar, 680],
    [fixturePaths.barLine, 343],
    [fixturePaths.barLine, 680],
    [fixturePaths.pie, 343],
    [fixturePaths.pie, 680],
  ])("keeps visible labels collision-free for %s at %ipx", (path, width) => {
    const visual = validVisual(reportFixture(path));
    const scene = buildStructuredVisualScene(visual, width);
    assertNoLabelOverlap(scene.labelBoxes);
    for (const box of scene.labelBoxes) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(scene.width + 0.001);
      expect(box.y + box.height).toBeLessThanOrEqual(scene.height + 0.001);
    }
  });

  it("is deterministic and preserves missing bars as explicit markers", () => {
    const visual = validVisual(reportFixture(fixturePaths.groupedBar));
    const first = buildStructuredVisualScene(visual, 343);
    const second = buildStructuredVisualScene(visual, 343);
    expect(first).toEqual(second);
    expect(first.missingValueCount).toBe(3);
    expect(first.commands).toContainEqual(expect.objectContaining({ type: "text", text: "缺" }));
  });

  it("breaks a line at null rather than connecting across missing evidence", () => {
    const payload = clonedFixture(fixturePaths.barLine);
    const series = payload.series as Array<Record<string, unknown>>;
    const line = series[1] ?? {};
    const rawValues: unknown = line.values;
    const values: Array<number | null> = Array.isArray(rawValues)
      ? rawValues.map((entry: unknown) => (typeof entry === "number" ? entry : null))
      : [];
    values[4] = null;
    series[1] = { ...line, values };
    const scene = buildStructuredVisualScene(validVisual(payload), 680);
    const lineSegments = scene.commands.filter(
      (command) => command.type === "polyline" && !command.closed,
    );
    expect(lineSegments).toHaveLength(2);
    expect(scene.missingValueCount).toBe(1);
  });

  it("renders 9×9 cells inside 13 frontend-owned panel frames", () => {
    const visual = validVisual(reportFixture(fixturePaths.binaryGrid));
    const scene = buildStructuredVisualScene(visual, 343);
    const frames = scene.commands.filter(
      (command) =>
        command.type === "rect" &&
        command.box.width >= 54 &&
        command.box.width === command.box.height,
    );
    const gridLines = scene.commands.filter((command) => command.type === "line");
    expect(frames).toHaveLength(13);
    expect(gridLines.length).toBeGreaterThanOrEqual(12 * 20);
    expect(scene.commands).toContainEqual(expect.objectContaining({ type: "text", text: "?" }));
  });

  it("uses standard responsive panels while keeping audited internal paths", () => {
    for (const path of [fixturePaths.topologyPaths, fixturePaths.lineArt]) {
      const visual = validVisual(reportFixture(path));
      const phone = buildStructuredVisualScene(visual, 343);
      const tablet = buildStructuredVisualScene(visual, 680);
      expect(phone.commands.filter((command) => command.type === "path").length).toBeGreaterThan(0);
      expect(tablet.commands.filter((command) => command.type === "path").length).toBeGreaterThan(
        0,
      );
      expect(phone.commands).toContainEqual(expect.objectContaining({ type: "text", text: "?" }));
      expect(tablet.width).toBe(680);
      expect(tablet.height).toBe(preferredCanvasHeight("pathPanels", 680));
    }
  });

  it("caps chart height on tablet instead of scaling rpx indefinitely", () => {
    expect(preferredCanvasHeight("groupedBar", 343)).toBeLessThan(
      preferredCanvasHeight("groupedBar", 680),
    );
    expect(preferredCanvasHeight("groupedBar", 1400)).toBe(350);
    expect(preferredCanvasHeight("binaryGrid", 1400)).toBe(520);
  });
});

function validVisual(input: unknown): ValidatedStructuredVisual {
  const validation = validateStructuredVisual(input);
  expect(validation.status).toBe("valid");
  if (validation.status !== "valid") throw new Error("Expected valid visual fixture.");
  return validation.visual;
}

function assertNoLabelOverlap(boxes: SceneLabelBox[]): void {
  boxes.forEach((box, index) => {
    boxes.slice(index + 1).forEach((other) => {
      expect(boxesOverlap(box, other, 1), `${box.id} overlaps ${other.id}`).toBe(false);
    });
  });
}
