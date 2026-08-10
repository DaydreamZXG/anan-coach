import { describe, expect, it } from "vitest";

import {
  validateStructuredVisual,
  type StructuredVisualValidation,
} from "../../miniprogram/design-system/structured-visuals/schema";
import { selectStructuredVisual } from "../../miniprogram/design-system/structured-visuals/registry";
import { clonedFixture, fixturePaths, reportFixture } from "./fixtures";

describe("structured visual schema", () => {
  it("accepts every audited prototype without inventing a discriminator", () => {
    const expectations = [
      [fixturePaths.groupedBar, "groupedBar"],
      [fixturePaths.barLine, "barLine"],
      [fixturePaths.pie, "pie"],
      [fixturePaths.binaryGrid, "binaryGrid"],
      [fixturePaths.topologyPaths, "pathPanels"],
      [fixturePaths.lineArt, "pathPanels"],
    ] as const;

    for (const [path, kind] of expectations) {
      const validation = validateStructuredVisual(reportFixture(path));
      expect(validation.status, path).toBe("valid");
      expect(validation.registration?.kind, path).toBe(kind);
    }
  });

  it("preserves explicit nulls and input evidence", () => {
    const validation = validResult(reportFixture(fixturePaths.groupedBar));
    expect(validation.visual.kind).toBe("groupedBar");
    if (validation.visual.kind !== "groupedBar") return;

    expect(validation.visual.series[3]?.values).toEqual([52802, null, null, null]);
    expect(validation.issues).toContainEqual(
      expect.objectContaining({
        severity: "warning",
        code: "NULL_VALUES_PRESERVED",
        path: "series[3].values",
      }),
    );
    expect(validation.evidence).toMatchObject({
      schemaVersion: "grouped-bar-chart-v1",
      sourceQuestionId: "cq_03ea803d569c90b5b17c072c",
    });
  });

  it("distinguishes a valid empty payload from malformed input", () => {
    const emptyPayload = clonedFixture(fixturePaths.groupedBar);
    emptyPayload.categories = [];
    emptyPayload.series = [];
    expect(validateStructuredVisual(emptyPayload)).toMatchObject({
      status: "empty",
      registration: { kind: "groupedBar" },
    });

    const malformed = clonedFixture(fixturePaths.groupedBar);
    const series = malformed.series as Array<Record<string, unknown>>;
    series[0] = { ...(series[0] ?? {}), values: [34658] };
    expect(errorCodes(validateStructuredVisual(malformed))).toContain(
      "CATEGORY_VALUE_LENGTH_MISMATCH",
    );
  });

  it("rejects pie angles or shares that do not close instead of normalizing them", () => {
    const angleGap = clonedFixture(fixturePaths.pie);
    const angleSlices = angleGap.slices as Array<Record<string, unknown>>;
    angleSlices[3] = {
      ...(angleSlices[3] ?? {}),
      endAngle: 350,
      sweepAngle: 75,
      share: 0.2083,
    };
    angleGap.boundariesDegrees = [0, 29, 193, 275, 350];
    expect(errorCodes(validateStructuredVisual(angleGap))).toEqual(
      expect.arrayContaining(["PIE_ANGLE_NOT_CLOSED", "PIE_BOUNDARIES_NOT_CLOSED"]),
    );

    const shareGap = clonedFixture(fixturePaths.pie);
    const shareSlices = shareGap.slices as Array<Record<string, unknown>>;
    shareSlices[0] = { ...(shareSlices[0] ?? {}), share: 0.02 };
    expect(errorCodes(validateStructuredVisual(shareGap))).toEqual(
      expect.arrayContaining(["PIE_SHARE_MISMATCH", "PIE_SHARE_NOT_CLOSED"]),
    );
  });

  it("requires an exact 9×9 binary occupancy", () => {
    const malformed = clonedFixture(fixturePaths.binaryGrid);
    const items = malformed.items as Array<Record<string, unknown>>;
    const first = items[0] ?? {};
    const occupancy = JSON.parse(JSON.stringify(first.occupancy)) as number[][];
    occupancy[0] = occupancy[0]?.slice(0, 8) ?? [];
    items[0] = { ...first, occupancy };

    expect(errorCodes(validateStructuredVisual(malformed))).toContain("BINARY_GRID_CELL_INVALID");
  });

  it("rejects unknown versions and unsupported path geometry", () => {
    expect(selectStructuredVisual("future-chart-v9")).toBeNull();
    expect(validateStructuredVisual({ schemaVersion: "future-chart-v9" })).toMatchObject({
      status: "invalid",
      registration: null,
      issues: [expect.objectContaining({ code: "UNKNOWN_SCHEMA_VERSION" })],
    });

    const curved = clonedFixture(fixturePaths.topologyPaths);
    const items = curved.items as Array<Record<string, unknown>>;
    items[0] = { ...(items[0] ?? {}), path: "M0 0 C5 5 10 10 15 15 Z" };
    expect(errorCodes(validateStructuredVisual(curved))).toContain("PATH_UNSUPPORTED");
  });
});

function validResult(input: unknown): Extract<StructuredVisualValidation, { status: "valid" }> {
  const validation = validateStructuredVisual(input);
  expect(validation.status).toBe("valid");
  if (validation.status !== "valid") throw new Error("Expected valid structured visual fixture.");
  return validation;
}

function errorCodes(validation: StructuredVisualValidation): string[] {
  expect(validation.status).toBe("invalid");
  return validation.issues.filter((issue) => issue.severity === "error").map((issue) => issue.code);
}
