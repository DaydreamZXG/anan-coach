import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const reportRoot = resolve(process.cwd(), "../../reports");

export const fixturePaths = {
  groupedBar: "data-analysis-chart-structure-test/structured-cq_03ea803d569c90b5b17c072c.json",
  barLine: "data-analysis-chart-structure-test/structured-mobile-production-combo-chart.json",
  pie: "pie-chart-structure-test/structured-pie-chart.json",
  binaryGrid: "graphic-reasoning-structure-test/structured-binary-grid-prototype.json",
  topologyPaths: "graphic-reasoning-structure-test/structured-line-matrix-prototype.json",
  lineArt: "graphic-reasoning-structure-test/structured-cq_09fc9cc57c86e57c553944c2.json",
  primitiveClassification:
    "structured-visuals-wechat-prototype/curve-arrow-wireframe/structured.json",
  glyphSequence: "structured-visuals-wechat-prototype/glyph-sequence/structured.json",
  chineseGlyphSequence:
    "structured-visuals-wechat-prototype/chinese-glyph-sequence/structured.json",
  lifeNeedsReview: "structured-visuals-wechat-prototype/life-outline-needs-review/structured.json",
  foldingNeedsReview: "structured-visuals-wechat-prototype/folding-needs-review/structured.json",
  shadedNeedsReview:
    "structured-visuals-wechat-prototype/shaded-polygons-needs-review/structured.json",
} as const;

export function reportFixture(path: string): unknown {
  return JSON.parse(readFileSync(resolve(reportRoot, path), "utf8")) as unknown;
}

export function clonedFixture(path: string): Record<string, unknown> {
  return JSON.parse(JSON.stringify(reportFixture(path))) as Record<string, unknown>;
}
