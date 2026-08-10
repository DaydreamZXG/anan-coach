import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { validateStructuredVisual } from "../../miniprogram/design-system/structured-visuals/schema";

const reportRoot = resolve(process.cwd(), "../../reports/structured-visuals-wechat-prototype");
const verifiedSamples = [
  "binary-grid",
  "straight-topology",
  "rare-vector",
  "curve-arrow-wireframe",
  "glyph-sequence",
  "chinese-glyph-sequence",
] as const;
const reviewSamples = [
  "life-outline-needs-review",
  "folding-needs-review",
  "shaded-polygons-needs-review",
] as const;

describe("real graphic-reasoning evidence", () => {
  it("keeps source, structure, redraw, comparison, and explicit acceptance checks together", () => {
    verifiedSamples.forEach((name) => {
      const directory = resolve(reportRoot, name);
      const structured = json(resolve(directory, "structured.json"));
      const acceptance = json(resolve(directory, "acceptance.json"));
      expect(existsSync(resolve(directory, "source.png")), name).toBe(true);
      expect(existsSync(resolve(directory, "redrawn.svg")), name).toBe(true);
      expect(
        existsSync(resolve(directory, "comparison.png")) ||
          existsSync(resolve(directory, "comparison.html")),
        name,
      ).toBe(true);
      expect(validateStructuredVisual(structured).status, name).toBe("valid");
      expect(acceptance.result, name).toMatch(/^verified/u);
      expect(fieldPass(acceptance, "panelCount"), name).toBe(true);
      expect(fieldPass(acceptance, "optionCount"), name).toBe(true);
      expect(fieldPass(acceptance, "outerFrame"), name).toBe(true);
      expect(fieldPass(acceptance, "majorTopology"), name).toBe(true);
      expect(fieldPass(acceptance, "textContent"), name).toBe(true);
      verifyHashWhenPresent(directory, structured);
    });
  });

  it("keeps unverified samples as evidence-backed review states without a redraw", () => {
    reviewSamples.forEach((name) => {
      const directory = resolve(reportRoot, name);
      const structured = json(resolve(directory, "structured.json"));
      const acceptance = json(resolve(directory, "acceptance.json"));
      expect(existsSync(resolve(directory, "source.png")), name).toBe(true);
      expect(existsSync(resolve(directory, "comparison.html")), name).toBe(true);
      expect(existsSync(resolve(directory, "redrawn.svg")), name).toBe(false);
      expect(validateStructuredVisual(structured).status, name).toBe("needs_review");
      expect(acceptance.result, name).toBe("needs_review");
      expect(fieldPass(acceptance, "majorTopology"), name).toBe(false);
      verifyHashWhenPresent(directory, structured);
    });
  });
});

function json(path: string): Record<string, unknown> {
  return JSON.parse(readFileSync(path, "utf8")) as Record<string, unknown>;
}

function fieldPass(record: Record<string, unknown>, key: string): boolean | undefined {
  const value = record[key];
  return typeof value === "object" && value !== null && "pass" in value
    ? (value as { pass?: boolean }).pass
    : undefined;
}

function verifyHashWhenPresent(directory: string, structured: Record<string, unknown>): void {
  const verification = structured.verification as Record<string, unknown> | undefined;
  const audit = structured.audit as Record<string, unknown> | undefined;
  const expected = verification?.sourceSha256 ?? audit?.sourceSha256;
  if (typeof expected !== "string") return;
  const actual = createHash("sha256")
    .update(readFileSync(resolve(directory, "source.png")))
    .digest("hex");
  expect(actual).toBe(expected);
}
