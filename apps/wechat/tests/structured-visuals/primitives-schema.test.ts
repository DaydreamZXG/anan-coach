import { describe, expect, it } from "vitest";

import { GRAPHIC_PRIMITIVE_LIMITS } from "../../miniprogram/design-system/structured-visuals/primitives";
import {
  validateStructuredVisual,
  type StructuredVisualValidation,
} from "../../miniprogram/design-system/structured-visuals/schema";
import { clonedFixture, fixturePaths, reportFixture } from "./fixtures";

describe("versioned graphic primitive schema", () => {
  it("accepts real semantic samples for curves, wireframes, letters, numbers, and Chinese glyphs", () => {
    for (const path of [
      fixturePaths.primitiveClassification,
      fixturePaths.glyphSequence,
      fixturePaths.chineseGlyphSequence,
    ]) {
      const validation = validateStructuredVisual(reportFixture(path));
      expect(validation.status, path).toBe("valid");
      expect(validation.registration).toMatchObject({
        schemaVersion: "graphic-primitives-v2",
        kind: "primitivePanels",
      });
    }
  });

  it("keeps unverified life, folding, and shaded samples out of Canvas", () => {
    for (const path of [
      fixturePaths.lifeNeedsReview,
      fixturePaths.foldingNeedsReview,
      fixturePaths.shadedNeedsReview,
    ]) {
      const validation = validateStructuredVisual(reportFixture(path));
      expect(validation.status, path).toBe("needs_review");
      expect(validation.issues).toContainEqual(
        expect.objectContaining({ code: "RENDERING_REQUIRES_REVIEW" }),
      );
    }

    const missingEvidence = clonedFixture(fixturePaths.lifeNeedsReview);
    delete missingEvidence.sourceAsset;
    const audit = missingEvidence.audit as Record<string, unknown>;
    delete audit.sourceSha256;
    expect(errorCodes(validateStructuredVisual(missingEvidence))).toEqual(
      expect.arrayContaining(["STRING_REQUIRED", "REVIEW_AUDIT_REQUIRED"]),
    );
  });

  it("validates every primitive, controlled transform, style, and evidenced vector group", () => {
    const payload = clonedFixture(fixturePaths.primitiveClassification);
    payload.fidelityTier = "deterministic_vector";
    const panels = payload.panels as Array<Record<string, unknown>>;
    const first = panels[0] ?? {};
    const primitives = first.primitives as Array<Record<string, unknown>>;
    primitives.push(
      {
        type: "polyline",
        points: [
          { x: 1, y: 2 },
          { x: 3, y: 4 },
        ],
      },
      { type: "roundedRect", box: { x: 5, y: 6, width: 20, height: 10 }, radius: 3 },
      {
        type: "quadraticBezier",
        from: { x: 0, y: 0 },
        control: { x: 5, y: 10 },
        to: { x: 10, y: 0 },
      },
      {
        type: "cubicBezier",
        from: { x: 0, y: 0 },
        control1: { x: 3, y: 10 },
        control2: { x: 7, y: -10 },
        to: { x: 10, y: 0 },
      },
      { type: "dot", center: { x: 8, y: 8 }, radius: 2 },
      {
        type: "face",
        points: [
          { x: 0, y: 0 },
          { x: 8, y: 0 },
          { x: 4, y: 8 },
        ],
      },
      {
        type: "textGlyph",
        content: "字A",
        position: { x: 15, y: 15 },
        fontCategory: "serif",
        fontStyle: "normal",
        fontWeight: 500,
        fontSize: 12,
        direction: "vertical",
        align: "center",
      },
      {
        type: "semanticSymbol",
        semanticLabel: "verified_life_outline",
        primitives: [
          {
            type: "pathGroup",
            paths: [
              {
                commands: [
                  { op: "move", x: 0, y: 0 },
                  {
                    op: "cubic",
                    control1X: 2,
                    control1Y: 8,
                    control2X: 8,
                    control2Y: 2,
                    x: 10,
                    y: 10,
                  },
                  { op: "close" },
                ],
                fillRule: "evenodd",
              },
            ],
            sourceEvidence: {
              sourceAsset: "verified-life-outline.png",
              extractionMethod: "manual_trace",
              verified: true,
            },
            style: {
              stroke: "#101828",
              fill: "rgba(16,24,40,0.2)",
              lineWidth: 2,
              dash: [4, 2],
              opacity: 0.7,
            },
            transform: {
              translateX: 2,
              translateY: -1,
              scaleX: 1.2,
              scaleY: 0.8,
              rotateDegrees: 15,
              originX: 5,
              originY: 5,
            },
          },
        ],
      },
    );
    panels[0] = { ...first, primitives };

    const validation = validateStructuredVisual(payload);
    expect(validation.status).toBe("valid");
    if (validation.status !== "valid" || validation.visual.kind !== "primitivePanels") return;
    expect(validation.visual.complexity.pathCommandCount).toBe(3);
    expect(validation.visual.complexity.estimatedDrawCommandCount).toBeGreaterThan(0);
    expect(validation.visual.fidelityTier).toBe("deterministic_vector");
  });

  it("rejects a pathGroup without provenance and a semantic tier containing vector-only contours", () => {
    const payload = clonedFixture(fixturePaths.primitiveClassification);
    const panels = payload.panels as Array<Record<string, unknown>>;
    const first = panels[0] ?? {};
    panels[0] = {
      ...first,
      primitives: [
        {
          type: "pathGroup",
          paths: [
            {
              commands: [
                { op: "move", x: 0, y: 0 },
                { op: "line", x: 2, y: 2 },
              ],
              fillRule: "nonzero",
            },
          ],
        },
      ],
    };
    expect(errorCodes(validateStructuredVisual(payload))).toEqual(
      expect.arrayContaining(["VECTOR_EVIDENCE_REQUIRED"]),
    );

    const evidenced = clonedFixture(fixturePaths.primitiveClassification);
    const evidencedPanels = evidenced.panels as Array<Record<string, unknown>>;
    evidencedPanels[0] = {
      ...(evidencedPanels[0] ?? {}),
      primitives: [
        {
          type: "pathGroup",
          paths: [
            {
              commands: [
                { op: "move", x: 0, y: 0 },
                { op: "line", x: 2, y: 2 },
              ],
              fillRule: "nonzero",
            },
          ],
          sourceEvidence: {
            sourceAsset: "source.png",
            extractionMethod: "verified_vector",
            verified: true,
          },
        },
      ],
    };
    expect(errorCodes(validateStructuredVisual(evidenced))).toContain(
      "SEMANTIC_TIER_CONTAINS_PATH_GROUP",
    );
  });

  it("rejects malformed coordinates, transforms, and bounded-complexity overflow", () => {
    const nonFinite = clonedFixture(fixturePaths.primitiveClassification);
    const nonFinitePanels = nonFinite.panels as Array<Record<string, unknown>>;
    nonFinitePanels[0] = {
      ...(nonFinitePanels[0] ?? {}),
      primitives: [{ type: "circle", center: { x: Number.POSITIVE_INFINITY, y: 1 }, radius: 2 }],
    };
    expect(errorCodes(validateStructuredVisual(nonFinite))).toContain("FINITE_NUMBER_REQUIRED");

    const unsafeTransform = clonedFixture(fixturePaths.primitiveClassification);
    const unsafePanels = unsafeTransform.panels as Array<Record<string, unknown>>;
    unsafePanels[0] = {
      ...(unsafePanels[0] ?? {}),
      primitives: [
        {
          type: "line",
          from: { x: 0, y: 0 },
          to: { x: 1, y: 1 },
          transform: {
            translateX: 0,
            translateY: 0,
            scaleX: 100,
            scaleY: 1,
            rotateDegrees: 0,
            originX: 0,
            originY: 0,
          },
        },
      ],
    };
    expect(errorCodes(validateStructuredVisual(unsafeTransform))).toContain(
      "PRIMITIVE_SCALE_LIMIT",
    );

    const overflow = clonedFixture(fixturePaths.primitiveClassification);
    const overflowPanels = overflow.panels as Array<Record<string, unknown>>;
    overflowPanels[0] = {
      ...(overflowPanels[0] ?? {}),
      primitives: Array.from(
        { length: GRAPHIC_PRIMITIVE_LIMITS.maxPrimitivesPerPanel + 1 },
        (_, index) => ({
          type: "line",
          from: { x: index % 100, y: 0 },
          to: { x: index % 100, y: 1 },
        }),
      ),
    };
    expect(errorCodes(validateStructuredVisual(overflow))).toContain(
      "PRIMITIVE_PANEL_COMPLEXITY_LIMIT",
    );

    const edgeOverflow = clonedFixture(fixturePaths.primitiveClassification);
    const edgePanels = edgeOverflow.panels as Array<Record<string, unknown>>;
    edgePanels[0] = {
      ...(edgePanels[0] ?? {}),
      primitives: Array.from({ length: 33 }, () => ({
        type: "wireframe",
        interpretation: "geometry_only",
        vertices: [
          { x: 0, y: 0 },
          { x: 1, y: 1 },
        ],
        edges: Array.from({ length: GRAPHIC_PRIMITIVE_LIMITS.maxWireframeEdges }, () => [0, 1]),
      })),
    };
    expect(errorCodes(validateStructuredVisual(edgeOverflow))).toEqual(
      expect.arrayContaining(["WIREFRAME_EDGE_TOTAL_LIMIT", "DRAW_COMMAND_TOTAL_LIMIT"]),
    );
  });
});

function errorCodes(validation: StructuredVisualValidation): string[] {
  expect(validation.status).toBe("invalid");
  return validation.issues.filter((issue) => issue.severity === "error").map((issue) => issue.code);
}
